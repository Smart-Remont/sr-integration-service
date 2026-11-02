"""``ClientSignController::performSignAction``: DOCX to PDF, a MyNCA process, a redirect to it."""

import base64
from typing import Any

from fastapi import HTTPException, Response, status
from loguru import logger

from src.config import ClientSignConfig
from src.shared.php_text import php_html_escape, php_int, php_is_truthy, php_string

from .callback_key import callback_key
from .client import MyncaAnswer, MyncaClient
from .doc_types import ID_COLUMN_BY_DOCUMENT, SIGN_TYPE_BY_DOCUMENT, DocumentType
from .documents import DocumentSource, signed_file_name
from .errors import ClientSignDatabaseError, ClientSignError
from .params import DocumentParams
from .repo import ClientSignDatabase, ClientSignRepository
from .responses import php_text_response, redirect_response

_PROCESS_LIFETIME_MINUTES = 60


class SigningService:
    def __init__(
        self,
        *,
        database: ClientSignDatabase,
        mynca: MyncaClient,
        documents: DocumentSource,
        config: ClientSignConfig,
    ) -> None:
        self._database = database
        self._mynca = mynca
        self._documents = documents
        self._config = config

    async def perform_sign(self, params: DocumentParams) -> Response:
        try:
            return redirect_response(await self._create_sign_process(params))
        except ClientSignError as exc:
            logger.warning("client-sign perform-sign refused")
            return php_text_response(_error_page(exc.message))

    async def _create_sign_process(self, params: DocumentParams) -> str:
        if params.uuid == "":
            raise ClientSignError("UUID документа не указан")
        try:
            doc_type = DocumentType(params.doc_type)
        except ValueError as exc:
            raise ClientSignError(f"Неизвестный тип документа: {params.doc_type}") from exc
        self._require_configuration()

        row = await self._document(doc_type, params.uuid)
        docx = await self._documents.read(doc_type, row)
        if not docx:
            raise ClientSignError("Не удалось получить документ для подписания")
        pdf_base64 = await self._convert_to_pdf(docx)
        answer = await self._mynca.sign_create(self._payload(doc_type, row, params, pdf_base64))
        return _sign_url(answer, doc_type)

    def _require_configuration(self) -> None:
        for name, value in (
            ("CLIENT_SIGN_MYNCA_BASE_URL", self._config.mynca_base_url),
            ("CLIENT_SIGN_MYNCA_TOKEN", self._config.mynca_token),
            ("CLIENT_SIGN_PUBLIC_URL", self._config.public_url),
            ("CLIENT_SIGN_CALLBACK_SECRET", self._config.callback_secret),
        ):
            if not value:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=f"{name} не настроен",
                )

    async def _document(self, doc_type: DocumentType, uuid: str) -> dict[str, Any] | None:
        async def read(repository: ClientSignRepository) -> dict[str, Any] | None:
            return await repository.document_by_uuid(doc_type, uuid)

        try:
            return await self._database.step(read)
        except ClientSignDatabaseError as exc:
            raise ClientSignError(f"Ошибка получения данных документа: {exc.message}") from exc

    async def _convert_to_pdf(self, docx: bytes) -> str:
        answer = await self._mynca.docx_to_pdf(docx)
        if not answer.status:
            message = answer.field("message")
            detail = (
                php_string(message)
                if message is not None
                else (answer.error if php_is_truthy(answer.error) else "Неизвестная ошибка")
            )
            raise ClientSignError(f"Ошибка конвертации docx→pdf: {detail}")
        pdf = answer.field("pdf")
        if pdf is None and not answer.is_json and php_is_truthy(answer.raw):
            pdf = base64.b64encode(answer.raw or b"").decode("ascii")
        if not php_is_truthy(pdf):
            raise ClientSignError("MyNCA API вернул пустой PDF")
        return php_string(pdf)

    def _payload(
        self,
        doc_type: DocumentType,
        row: dict[str, Any] | None,
        params: DocumentParams,
        pdf_base64: str,
    ) -> dict[str, object]:
        values = row or {}
        ext_id = php_int(values.get(ID_COLUMN_BY_DOCUMENT[doc_type]))
        sign_type_code = SIGN_TYPE_BY_DOCUMENT[doc_type]
        key = callback_key(self._config.callback_secret, sign_type_code, ext_id)
        public_url = self._config.public_url
        return {
            "file_name": signed_file_name(doc_type, row, "pdf"),
            "file_content": pdf_base64,
            "ext_id": ext_id,
            # MyNCA returns meta_data unchanged in the callback.
            "meta_data": {
                "type_code": sign_type_code,
                "doc_type": doc_type,
                "hash": params.hash,
                "uuid": params.uuid,
                "client_request_id": _int_or_none(values.get("client_request_id")),
                "ds_id": _int_or_none(values.get("ds_id")),
                "client_request_document_id": _int_or_none(
                    values.get("client_request_document_id")
                ),
            },
            "back_url": f"{public_url}/client-sign/callback/key/{key}",
            "return_url": f"{public_url}/client-sign/sign-doc-list/remont/{params.hash}",
            "exp_minutes": _PROCESS_LIFETIME_MINUTES,
        }


def _sign_url(answer: MyncaAnswer, doc_type: DocumentType) -> str:
    nested = answer.field("data")
    sign_url = nested.get("sign_url") if isinstance(nested, dict) else None
    logger.info(
        "MyNCA sign/create | doc_type={doc_type} code={code}", doc_type=doc_type, code=answer.code
    )
    if answer.status and sign_url is not None:
        return php_string(sign_url)
    message = answer.field("message")
    if message is not None:
        detail = php_string(message)
    elif answer.error != "":
        detail = answer.error
    else:
        detail = f"Неизвестная ошибка MyNCA API (code={answer.code})"
    raise ClientSignError(f"Ошибка создания процесса подписания: {detail}")


def _int_or_none(value: object) -> int | None:
    return None if value is None else php_int(value)


def _error_page(message: str) -> str:
    return (
        '<div style="padding:40px;font-family:sans-serif;">'
        '<h3 style="color:#c00;">Ошибка подписания</h3>'
        f"<p>{php_html_escape(message)}</p>"
        '<p><a href="javascript:history.back();">Вернуться назад</a></p>'
        "</div>"
    )
