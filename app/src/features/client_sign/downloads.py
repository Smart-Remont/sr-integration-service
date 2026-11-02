"""Document files of ``ClientSignController`` and ``IntegrationController``."""

import base64
import re
from collections.abc import Awaitable
from typing import Any

from fastapi import HTTPException, Response, status
from loguru import logger

from src.config import ClientSignConfig
from src.php_compat.text import php_html_escape, php_is_truthy, php_string

from .client import DocumentFetcher, MyncaAnswer, MyncaClient
from .doc_types import AGREEMENT_MANUAL, DocumentType
from .documents import DocumentSource, document_id, signed_file_name
from .errors import ClientSignDatabaseError, ClientSignError
from .params import DocumentParams
from .repo import ClientSignDatabase, ClientSignRepository
from .responses import (
    CMS_CONTENT_TYPE,
    PDF_CONTENT_TYPE,
    file_response,
    json_body_response,
    php_text_response,
    redirect_response,
)

_UNKNOWN_TYPE = "Неизвестный тип документа"
_PARTNER_FILE_NAME = "Подписанный документ.pdf"
_BAD_REQUEST = 400
_NOT_BASE64 = re.compile(rb"[^A-Za-z0-9+/]")
_BASE64_BLOCK = 4


class DownloadService:
    def __init__(
        self,
        *,
        database: ClientSignDatabase,
        mynca: MyncaClient,
        documents: DocumentSource,
        fetcher: DocumentFetcher,
        config: ClientSignConfig,
    ) -> None:
        self._database = database
        self._mynca = mynca
        self._documents = documents
        self._fetcher = fetcher
        self._config = config

    async def download(self, params: DocumentParams) -> Response:
        if params.doc_type == AGREEMENT_MANUAL:
            return redirect_response(params.redirect_uuid)
        return await _with_error_text(self._download(params), "download")

    async def download_signed(self, params: DocumentParams) -> Response:
        return await _with_error_text(self._download_signed(params), "download-signed")

    async def download_cms(self, params: DocumentParams) -> Response:
        return await _with_error_text(self._download_cms(params), "download-cms")

    async def integration_download(self, doc_type: DocumentType, uuid: str) -> Response:
        """``downloadDsAction`` and ``downloadAgreementAction``.

        An unsigned document goes through LibreOffice on the old server. The service has no such
        tool, so that branch answers 503 until it is decided how to convert (see STATUS.md).
        """
        error_text = ""
        row: dict[str, Any] | None = None
        try:
            row = await self._row(doc_type, uuid)
        except ClientSignDatabaseError as exc:
            error_text = exc.message
        if row is None:
            # PHP goes on with no row, gets no DOCX for an empty id and answers 400 after the text.
            return json_body_response(error_text, _BAD_REQUEST)
        if row.get("signed"):
            return await self._partner_file(doc_type, row)
        logger.warning("Unsigned document conversion is not available | type={t}", t=doc_type)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Конвертация DOCX в PDF не настроена",
        )

    async def _download(self, params: DocumentParams) -> Response:
        doc_type = _known_type(params.doc_type)
        row = await self._row_or_error(doc_type, params.uuid)
        if row and row.get("signed"):
            return await self._signed_pdf(doc_type, row)
        docx = await self._documents.read(doc_type, row)
        if not docx:
            raise ClientSignError("Не удалось получить документ")
        answer = await self._mynca_client().docx_to_pdf(docx)
        pdf = answer.field("pdf")
        if not answer.status or pdf is None:
            message = answer.field("message")
            raise ClientSignError("Ошибка конвертации" if message is None else php_string(message))
        return file_response(
            _php_base64_decode(php_string(pdf)),
            PDF_CONTENT_TYPE,
            signed_file_name(doc_type, row, "pdf"),
        )

    async def _download_signed(self, params: DocumentParams) -> Response:
        doc_type = _known_type(params.doc_type)
        row = await self._row_or_error(doc_type, params.uuid)
        return await self._signed_pdf(doc_type, row)

    async def _download_cms(self, params: DocumentParams) -> Response:
        doc_type = _known_type(params.doc_type)
        row = await self._row_or_error(doc_type, params.uuid)
        group_id = php_string((row or {}).get("sign_group_id"))
        if not php_is_truthy(group_id):
            raise ClientSignError("sign_group_id отсутствует")
        answer = await self._mynca_client().group_download_cms(group_id)
        if not answer.status or not php_is_truthy(answer.raw):
            raise ClientSignError(
                f"Ошибка MyNCA: {_mynca_error(answer, 'Не удалось получить CMS')}"
            )
        return file_response(
            answer.raw or b"",
            CMS_CONTENT_TYPE,
            signed_file_name(doc_type, row, "cms"),
        )

    async def _signed_pdf(self, doc_type: DocumentType, row: dict[str, Any] | None) -> Response:
        """``downloadSignedPdf``: MyNCA by group, and the partner backend for everything else."""
        group_id = php_string((row or {}).get("sign_group_id"))
        if php_is_truthy(group_id):
            answer = await self._mynca_client().group_download_pdf(group_id)
            if answer.status and php_is_truthy(answer.raw):
                return file_response(
                    answer.raw or b"",
                    PDF_CONTENT_TYPE,
                    signed_file_name(doc_type, row, "pdf"),
                )
            logger.warning(
                "MyNCA signed PDF failed, trying the partner | code={code}", code=answer.code
            )
        return await self._partner_file(doc_type, row)

    async def _partner_file(self, doc_type: DocumentType, row: dict[str, Any] | None) -> Response:
        """``getSignedFile``: PHP sends no Content-Length here, and any partner answer is the file."""
        if not self._config.partner_url:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="CLIENT_SIGN_PARTNER_URL не настроен",
            )
        url = (
            f"{self._config.partner_url}/get_signed_file/"
            f"{document_id(doc_type, row)}/{doc_type}/SIGNED/"
        )
        content = await self._fetcher.partner_signed_file(url)
        return file_response(content, PDF_CONTENT_TYPE, _PARTNER_FILE_NAME, send_length=False)

    def _mynca_client(self) -> MyncaClient:
        if not self._config.mynca_base_url or not self._config.mynca_token:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="CLIENT_SIGN_MYNCA_BASE_URL или CLIENT_SIGN_MYNCA_TOKEN не настроен",
            )
        return self._mynca

    async def _row_or_error(self, doc_type: DocumentType, uuid: str) -> dict[str, Any] | None:
        try:
            return await self._row(doc_type, uuid)
        except ClientSignDatabaseError as exc:
            raise ClientSignError(exc.message) from exc

    async def _row(self, doc_type: DocumentType, uuid: str) -> dict[str, Any] | None:
        async def read(repository: ClientSignRepository) -> dict[str, Any] | None:
            return await repository.document_by_uuid(doc_type, uuid)

        return await self._database.step(read)


async def _with_error_text(answer: Awaitable[Response], route: str) -> Response:
    try:
        return await answer
    except ClientSignError as exc:
        logger.warning("client-sign {route} refused", route=route)
        return php_text_response(f"Ошибка: {php_html_escape(exc.message)}")


def _known_type(doc_type: str) -> DocumentType:
    try:
        return DocumentType(doc_type)
    except ValueError as exc:
        raise ClientSignError(_UNKNOWN_TYPE) from exc


def _mynca_error(answer: MyncaAnswer, default: str) -> str:
    return answer.error if answer.error != "" else default


def _php_base64_decode(text: str) -> bytes:
    """``base64_decode`` without strict: other characters are dropped and padding is optional."""
    cleaned = _NOT_BASE64.sub(b"", text.encode("utf-8", "surrogateescape"))
    usable = len(cleaned) - (len(cleaned) % _BASE64_BLOCK == 1)
    padding = b"=" * (-usable % _BASE64_BLOCK)
    return base64.b64decode(cleaned[:usable] + padding)
