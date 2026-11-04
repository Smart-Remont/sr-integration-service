"""PHP ``cronAutoUploadSignDocAction`` and ``sign_document_upd``: the signed PDF goes into the request.

A run only reads from MyNCA, so a failed item is repeated by the next run without harm: at worst a file
stays unreferenced in storage. The lock matters because of the write: with ``sign_cnt`` other than 2
the function inserts a document row and, for 1, a task, and overlapping runs would add them twice.
"""

import asyncio
from datetime import date

from fastapi import Response
from loguru import logger

from src.php_compat.pdo_errors import php_error_state
from src.php_compat.text import php_int
from src.repository.base import SpRow
from src.shared.cron_run import run_stop_requested
from src.shared.mynca_client import MyncaClient
from src.storage.minio_client import put_object
from src.storage.modes import logical_path_to_object_key, php_uniqid

from .repo import AutoSignRepository
from .responses import empty_cron_response
from .run import read_rows_under_run_lock

_RUN_NAME = "auto-sign:cron-auto-upload-sign-doc"
_SIGNED_ONCE = 1
_SIGNED_TWICE = 2
_PDF_TYPE = "application/pdf"
# The S3 client retries by itself, and a hung storage must not stop the cron.
_STORE_SECONDS = 120.0


class DocumentUploadService:
    def __init__(self, repository: AutoSignRepository, mynca: MyncaClient) -> None:
        self._repository = repository
        self._mynca = mynca

    async def upload_signed_documents(self) -> Response:
        rows = await read_rows_under_run_lock(
            self._repository,
            _RUN_NAME,
            AutoSignRepository.sign_read_for_doc,
        )
        for row in rows:
            if run_stop_requested():
                logger.warning("Auto upload: stopped between rows, the service is stopping")
                break
            await self._upload_row(row)
        return empty_cron_response()

    async def _upload_row(self, row: SpRow) -> None:
        client_request_id = row.get("agreement_client_request_id")
        try:
            await self._upload_document(int(str(client_request_id)), php_int(row.get("sign_cnt")))
        except Exception as exc:
            logger.error(
                "Auto upload item failed | client_request_id={client_request_id} error={error}",
                client_request_id=client_request_id,
                error=php_error_state(exc),
            )

    async def _upload_document(self, client_request_id: int, sign_cnt: int) -> None:
        async with self._repository.step():
            group = await self._repository.sign_group_id_get_by_client_request(client_request_id)
        group_id = group.get("sign_group_id") if group else None
        if not group_id:
            logger.error(
                "Auto upload: no sign group | client_request_id={client_request_id}",
                client_request_id=client_request_id,
            )
            return
        answer = await self._mynca.group_download_pdf(str(group_id))
        if not answer.status or not answer.has_file:
            logger.error(
                "Auto upload: signed file not received | "
                "client_request_id={client_request_id} code={code}",
                client_request_id=client_request_id,
                code=answer.code,
            )
            return
        document_url = _new_document_path()
        async with asyncio.timeout(_STORE_SECONDS):
            await put_object(
                key=logical_path_to_object_key(document_url),
                body=answer.raw,
                content_type=_PDF_TYPE,
            )
        async with self._repository.step():
            await self._repository.sign_document_upd(
                client_request_id=client_request_id,
                sign_doc_name=f"Договор_оказания_услуг_{client_request_id}.pdf",
                sign_doc_url=document_url,
                is_create_task=sign_cnt == _SIGNED_ONCE,
                is_update=sign_cnt == _SIGNED_TWICE,
            )
        logger.info(
            "Auto upload: done | client_request_id={client_request_id}",
            client_request_id=client_request_id,
        )


def _new_document_path() -> str:
    return f"/documents/{date.today():%Y.%m.%d}/client_request_docs/{php_uniqid('cr_doc')}.pdf"
