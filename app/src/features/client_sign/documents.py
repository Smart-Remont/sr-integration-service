"""Where each unsigned document comes from: what ``getDocUrl`` and ``getDocContent`` do in PHP."""

from typing import Any

from fastapi import HTTPException, status
from loguru import logger

from src.config import ClientSignConfig, minio_config
from src.php_compat.text import php_int, php_is_truthy, php_string
from src.storage.minio_client import get_object
from src.storage.modes import logical_path_to_object_key

from .client import DocumentFetcher
from .doc_types import FILE_NAME_PREFIX_BY_DOCUMENT, ID_COLUMN_BY_DOCUMENT, DocumentType

# Two hand-made documents of one request that PHP serves from the site root.
_FIXED_TK_CHANGE_DS_ID = 48034
_FIXED_ROOM_CHANGE_DS_ID = 47606
_FIXED_TK_CHANGE_FILE = "/2601153_ds_tk_change.docx"
_FIXED_ROOM_CHANGE_FILE = "/2601153_ds_room_change.docx"


def document_id(doc_type: DocumentType, row: dict[str, Any] | None) -> str:
    return php_string((row or {}).get(ID_COLUMN_BY_DOCUMENT[doc_type]))


def signed_file_name(doc_type: DocumentType, row: dict[str, Any] | None, extension: str) -> str:
    return f"{FILE_NAME_PREFIX_BY_DOCUMENT[doc_type]}{document_id(doc_type, row)}.{extension}"


class DocumentSource:
    def __init__(self, config: ClientSignConfig, fetcher: DocumentFetcher) -> None:
        self._config = config
        self._fetcher = fetcher

    async def read(self, doc_type: DocumentType, row: dict[str, Any] | None) -> bytes | None:
        """The DOCX to convert, or ``None`` where PHP got ``false``."""
        if doc_type is DocumentType.OTBASY_STATEMENT:
            stored = await _read_stored_file(php_string((row or {}).get("document_url")))
            if stored:
                return stored
        return await self._fetcher.file_get_contents(self._document_url(doc_type, row))

    async def read_for_integration(
        self,
        doc_type: DocumentType,
        row: dict[str, Any],
        for_view: str,
    ) -> bytes | None:
        """The DOCX of ``downloadDsAction`` and ``downloadAgreementAction``: other addresses."""
        return await self._fetcher.file_get_contents(
            self._integration_url(doc_type, row, for_view),
        )

    def _integration_url(
        self,
        doc_type: DocumentType,
        row: dict[str, Any],
        for_view: str,
    ) -> str:
        request_id = php_string(row.get("client_request_id"))
        if doc_type is DocumentType.AGREEMENT:
            return (
                f"{self._office()}/dict/report/mode/contract/is_pdf/false"
                f"/for_view/{for_view}/client_request_id/{request_id}"
            )
        ds_id = php_string(row.get("ds_id"))
        type_code = row.get("ds_type_code")
        if type_code == "AGREEMENT_BARTER":
            return f"{self._office()}/dict/report/mode/ds_agreement_barter/ds_id/{ds_id}/doc/1"
        if type_code == "ROOM_CHANGE":
            query = "" if php_is_truthy(for_view) else "?is_sign=true"
            return (
                f"{self._myspace_api()}/client_request/{request_id}/ds/{ds_id}"
                f"/document/download/{query}"
            )
        return (
            f"{self._office()}/dict/ds-download/for_view/{for_view}"
            f"/ds_id/{ds_id}/client_request_id/{request_id}"
        )

    def _document_url(self, doc_type: DocumentType, row: dict[str, Any] | None) -> str:
        values = row or {}
        if doc_type is DocumentType.AGREEMENT:
            request_id = php_string(values.get("client_request_id"))
            return (
                f"{self._office()}/dict/report/mode/contract/is_pdf/false/for_view/false"
                f"/client_request_id/{request_id}"
            )
        if doc_type is DocumentType.OTBASY_STATEMENT:
            return f"{self._office()}{php_string(values.get('document_url'))}"
        return self._ds_url(values)

    def _ds_url(self, row: dict[str, Any]) -> str:
        ds_id = php_string(row.get("ds_id"))
        request_id = php_string(row.get("client_request_id"))
        type_code = row.get("ds_type_code")
        if php_int(row.get("ds_id")) == _FIXED_TK_CHANGE_DS_ID:
            return f"{self._office()}{_FIXED_TK_CHANGE_FILE}"
        if type_code == "AGREEMENT_BARTER":
            return f"{self._office()}/dict/report/mode/ds_agreement_barter/ds_id/{ds_id}/doc/1"
        if type_code != "ROOM_CHANGE":
            return (
                f"{self._office()}/dict/ds-download/is_pdf/false/for_view/false"
                f"/ds_id/{ds_id}/client_request_id/{request_id}"
            )
        if php_int(row.get("ds_id")) == _FIXED_ROOM_CHANGE_DS_ID:
            return f"{self._office()}{_FIXED_ROOM_CHANGE_FILE}"
        return (
            f"{self._myspace_api()}/client_request/{request_id}/ds/{ds_id}"
            "/document/download/?is_sign=true"
        )

    def _office(self) -> str:
        return _required(self._config.office_url, "CLIENT_SIGN_OFFICE_URL")

    def _myspace_api(self) -> str:
        return _required(self._config.myspace_api_url, "CLIENT_SIGN_MYSPACE_API_URL")


async def _read_stored_file(document_url: str) -> bytes | None:
    """``storage_read_file`` without its local disk: the service has only MinIO."""
    if not document_url or not minio_config.is_configured:
        return None
    try:
        return await get_object(logical_path_to_object_key(document_url))
    except Exception as exc:
        logger.warning("Stored document read failed | error={error}", error=type(exc).__name__)
        return None


def _required(value: str, name: str) -> str:
    if not value:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"{name} не настроен",
        )
    return value
