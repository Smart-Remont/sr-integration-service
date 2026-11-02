"""``ClientSignController::callbackAction``: MyNCA reports a signature, and it is recorded once."""

import re
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Response, status
from loguru import logger

from src.php_compat.json_codec import php_json_decode
from src.php_compat.text import php_int, php_is_positive, php_string, php_strtoupper
from src.php_compat.zend_request import PhpParam

from .callback_key import is_callback_key_valid
from .doc_types import DocumentType, SignTypeCode
from .errors import ClientSignDatabaseError
from .repo import ClientSignDatabase, ClientSignRepository
from .responses import json_answer

_SIGNED_STATUS = "SUCCESS"
_UNPROCESSABLE = 422
_CONFLICT = 409
_FORBIDDEN = 403
# Stricter than the uuid type on purpose: this is the form MyNCA sends, and a value that passes
# here can never fail in client.sign_tab__modify after the signature row exists.
_UUID_TEXT = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
# How client.insert_sign_general stores a report. A second signer of a CLIENT_CHANGE DS comes in as
# CLIENT_DS_SIGN and the function records it as CLIENT_DS_SIGN_SECOND.
_STORED_AS = {
    SignTypeCode.DS: ("ds_id", {"CLIENT_DS_SIGN", "CLIENT_DS_SIGN_SECOND"}),
    SignTypeCode.AGREEMENT: ("agreement_client_request_id", {"CLIENT_SIGN"}),
    SignTypeCode.OTBASY_STATEMENT: ("client_request_document_id", {"OTBASY_STATEMENT_SIGN"}),
}


@dataclass(frozen=True)
class SignatureReport:
    sign_process_id: str
    sign_group_id: str | None
    dn_name: str
    sign_type_code: SignTypeCode
    document_id: int | None

    def column(self, sign_type_code: SignTypeCode) -> int | None:
        return self.document_id if self.sign_type_code is sign_type_code else None


class CallbackRefused(Exception):
    """Nothing was recorded. ``message`` goes to MyNCA only, because it can name the signer's IIN."""

    def __init__(
        self,
        message: str,
        *,
        reason: str,
        status_code: int = 500,
        report: SignatureReport | None = None,
        error_code: str | None = None,
    ) -> None:
        super().__init__(reason)
        self.message = message
        self.reason = reason
        self.status_code = status_code
        self.report = report
        self.error_code = error_code


class SignCallbackService:
    def __init__(self, database: ClientSignDatabase, secret: str) -> None:
        self._database = database
        self._secret = secret

    async def handle(self, raw_body: bytes, key: PhpParam | None) -> Response:
        if not self._secret:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="CLIENT_SIGN_CALLBACK_SECRET не настроен",
            )
        try:
            return await self._process(raw_body, key)
        except CallbackRefused as exc:
            _log_refusal(exc)
            return json_answer({"status": False, "error": exc.message}, exc.status_code)

    async def _process(self, raw_body: bytes, key: PhpParam | None) -> Response:
        data = php_json_decode(raw_body)
        if not isinstance(data, dict | list):
            raise CallbackRefused("Невалидный JSON в callback", reason="invalid_json")
        fields: dict[str, Any] = data if isinstance(data, dict) else {}
        if fields.get("sign_process_id") is None:
            raise CallbackRefused("sign_process_id не указан", reason="no_process_id")
        if php_string(fields.get("status")) != _SIGNED_STATUS:
            return _accepted()

        report = _signature_report(fields)
        if report is None:
            logger.warning(
                "client-sign callback: no document type in meta_data | process={process}",
                process=_loggable_process(php_string(fields["sign_process_id"])),
            )
            return _accepted()
        if not self._is_sender_valid(report, key):
            raise CallbackRefused(
                "Колбэк не от MyNCA: неверный ключ в адресе",
                reason="wrong_key",
                status_code=_FORBIDDEN,
                report=report,
            )
        _require_uuids(report)
        await self._record_once(report)
        return _accepted()

    def _is_sender_valid(self, report: SignatureReport, key: PhpParam | None) -> bool:
        return isinstance(key, str) and is_callback_key_valid(
            self._secret, report.sign_type_code, report.document_id, key
        )

    async def _record_once(self, report: SignatureReport) -> None:
        async def record(repository: ClientSignRepository) -> object | None:
            return await _record_signature(repository, report)

        try:
            sign_id = await self._database.step(record)
        except ClientSignDatabaseError as exc:
            raise CallbackRefused(
                f"Ошибка insert_sign_general: {exc.message}",
                reason="database",
                report=report,
                error_code=exc.error_code,
            ) from exc
        # After the commit: "recorded" in the log means the row is there.
        logger.info(
            "client-sign callback recorded | sign_id={sign_id} process={process} type={type} "
            "document={document}",
            sign_id=sign_id,
            process=report.sign_process_id,
            type=report.sign_type_code,
            document=report.document_id,
        )


async def _record_signature(
    repository: ClientSignRepository,
    report: SignatureReport,
) -> object | None:
    """All in one transaction: a retry after a failure finds nothing, after a success finds the row.

    A repeat of the same document answers success. A DS or a statement would otherwise hit the
    unique index and answer 500, which makes MyNCA roll back a signature that sign_tab already
    holds, and an agreement, which has no such index, would get a second row.
    A process recorded on another document is not a repeat: this callback's signature is not
    stored, so success would be a lie. The row also says the earlier callback was false.
    """
    await repository.lock_sign_process(report.sign_process_id)
    stored = await repository.sign_by_process(report.sign_process_id)
    if stored is not None:
        return _answer_to_repeat(report, stored)
    sign_id = await repository.insert_sign_general(
        client_request_id=report.column(SignTypeCode.AGREEMENT),
        ds_id=report.column(SignTypeCode.DS),
        client_request_document_id=report.column(SignTypeCode.OTBASY_STATEMENT),
        sign_type_code=report.sign_type_code,
        dn_name=report.dn_name,
    )
    try:
        await repository.attach_sign_process(
            sign_id=sign_id,
            sign_process_id=report.sign_process_id,
            sign_group_id=report.sign_group_id,
        )
    except ClientSignDatabaseError as exc:
        # The whole transaction rolls back: a row without its process could not be found by a retry.
        raise CallbackRefused(
            f"Ошибка sign_tab__modify: {exc.message}",
            reason="attach_failed",
            report=report,
            error_code=exc.error_code,
        ) from exc
    return sign_id


def _answer_to_repeat(report: SignatureReport, stored: dict[str, Any]) -> object | None:
    if not _is_same_document(report, stored):
        raise CallbackRefused(
            "Процесс подписания уже записан на другой документ",
            reason="process_on_other_document",
            status_code=_CONFLICT,
            report=report,
            error_code=f"stored_sign_id={stored.get('sign_id')}",
        )
    logger.info(
        "client-sign callback repeated | sign_id={sign_id} process={process}",
        sign_id=stored.get("sign_id"),
        process=report.sign_process_id,
    )
    return stored.get("sign_id")


def _is_same_document(report: SignatureReport, stored: dict[str, Any]) -> bool:
    column, stored_types = _STORED_AS[report.sign_type_code]
    return (
        report.document_id is not None
        and stored.get(column) == report.document_id
        and stored.get("sign_type_code") in stored_types
    )


def _require_uuids(report: SignatureReport) -> None:
    """Before any write. MyNCA sends UUIDs, so anything else is refused rather than half stored."""
    if not _is_uuid(report.sign_process_id):
        raise CallbackRefused(
            "sign_process_id не UUID",
            reason="invalid_process_id",
            status_code=_UNPROCESSABLE,
            report=report,
        )
    if report.sign_group_id is not None and not _is_uuid(report.sign_group_id):
        raise CallbackRefused(
            "group_id не UUID",
            reason="invalid_group_id",
            status_code=_UNPROCESSABLE,
            report=report,
        )


def _signature_report(fields: dict[str, Any]) -> SignatureReport | None:
    meta = fields.get("meta_data")
    meta = meta if isinstance(meta, dict) else {}
    ext_id = None if fields.get("ext_id") is None else php_int(fields["ext_id"])
    type_code = php_string(meta.get("type_code"))
    doc_type = php_strtoupper(php_string(meta.get("doc_type")))

    if type_code == SignTypeCode.DS or doc_type == DocumentType.DS:
        sign_type, id_key = SignTypeCode.DS, "ds_id"
    elif type_code == SignTypeCode.OTBASY_STATEMENT or doc_type == DocumentType.OTBASY_STATEMENT:
        sign_type, id_key = SignTypeCode.OTBASY_STATEMENT, "client_request_document_id"
    elif type_code == SignTypeCode.AGREEMENT or doc_type == DocumentType.AGREEMENT:
        sign_type, id_key = SignTypeCode.AGREEMENT, "client_request_id"
    else:
        return None
    meta_id = meta.get(id_key)
    return SignatureReport(
        sign_process_id=php_string(fields["sign_process_id"]),
        # Absent or empty is a process without a group: real rows in sign_tab have a NULL here.
        sign_group_id=php_string(fields.get("group_id")) or None,
        dn_name=php_string(fields.get("dn_name")),
        sign_type_code=sign_type,
        document_id=php_int(meta_id) if php_is_positive(meta_id) else ext_id,
    )


def _log_refusal(refusal: CallbackRefused) -> None:
    report = refusal.report
    logger.warning(
        "client-sign callback refused | reason={reason} process={process} type={type} "
        "document={document} error={error}",
        reason=refusal.reason,
        process=_loggable_process(report.sign_process_id) if report else None,
        type=report.sign_type_code if report else None,
        document=report.document_id if report else None,
        error=refusal.error_code,
    )


def _loggable_process(value: str) -> str:
    # The value comes from the body and any caller can send text there, a signer's IIN included.
    return value if _is_uuid(value) else "not-a-uuid"


def _is_uuid(value: str) -> bool:
    return _UUID_TEXT.fullmatch(value) is not None


def _accepted() -> Response:
    return json_answer({"status": True})
