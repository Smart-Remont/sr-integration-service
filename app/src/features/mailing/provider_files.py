"""``provider-send-email-requests``: tables of unshipped and unprocessed requests, one row a provider.

It only fills the queue ``provider_send_email_tab`` and writes the PDFs. Nothing sends this queue:
``provider.read_provider_send_email`` has ``1=2`` and ``rest/provider-send-email`` is not called, the
last letter went on 11.07.2023. See STATUS.md, the owner decides whether this cron is needed at all.

One provider takes seconds, a run about ten minutes: no transaction stays open for that time.
"""

import asyncio
import random
import time
from pathlib import Path

from fastapi import Response
from loguru import logger

from src.php_compat.text import php_string
from src.repository.base import SpRow

from .pdf import PdfError, html_to_pdf
from .repo import MailingRepository
from .responses import cron_response
from .steps import DatabaseGoneError, MailingSteps
from .views import render_provider_table

PROVIDER_FILES_LOCK = "provider-send-email-requests"
_NOT_SHIPPED = "not_shipped"
_NOT_PROCESSED = "not_processed"
_TABLES = (
    (_NOT_SHIPPED, "Неотгруженные заявки"),
    (_NOT_PROCESSED, "Необработанные заявки"),
)
_FILES_URL = "/documents/provider_email_files/"


def php_uniqid_more_entropy(prefix: str) -> str:
    """``uniqid($prefix, true)``: the time in hex, then a number with 8 decimals."""
    seconds, micro = divmod(time.time_ns() // 1000, 1_000_000)
    # Not a secret: only the form of the name, which PHP gets from its own weak generator.
    return f"{prefix}{seconds:08x}{micro:05x}{random.random() * 10:.8f}"  # noqa: S311


class ProviderFilesService:
    def __init__(self, repository: MailingRepository, public_dir: str) -> None:
        self.steps = MailingSteps(repository)
        self.public_dir = public_dir

    async def create_requests(self) -> Response:
        if not await self.steps.take_lock(PROVIDER_FILES_LOCK):
            return cron_response()
        providers = await self.steps.value_or(
            "read_provider_for_unload",
            [],
            lambda repository: repository.read_provider_for_unload(),
        )
        try:
            for provider in providers:
                await self._provider_request(provider)
        except DatabaseGoneError:
            logger.error("provider-send-email-requests stopped: the database is gone")
        return cron_response()

    async def _provider_request(self, provider: SpRow) -> None:
        provider_id = provider["provider_id"]
        files: list[tuple[str, str]] = []
        for status_code, status_name in _TABLES:
            html = await self._table(provider, status_code, status_name)
            if html:
                url = await self._write_pdf(provider_id, html)
                files.append((f"{status_name}.pdf", url))
        if not files:
            return
        queue_id = await self._queue_row(provider)
        if queue_id is None:
            return
        for name, url in files:
            await self.steps.value_or(
                "insert_provider_send_email_attachment",
                None,
                lambda repository, name=name, url=url: (
                    repository.insert_provider_send_email_attachment(queue_id, name, url)
                ),
            )

    async def _table(self, provider: SpRow, status_code: str, status_name: str) -> str:
        rows = await self.steps.value_or(
            "read_provider_request_remont_unload",
            [],
            lambda repository: repository.read_provider_request_remont_unload(
                status_code, provider["provider_id"]
            ),
        )
        return render_provider_table(
            provider_name=provider.get("provider_name"),
            status_name=status_name,
            rows=rows,
        )

    async def _queue_row(self, provider: SpRow) -> object | None:
        return await self.steps.value_or(
            "insert_provider_send_email",
            None,
            lambda repository: repository.insert_provider_send_email(
                provider["provider_id"], provider.get("provider_email")
            ),
        )

    async def _write_pdf(self, provider_id: object, html: str) -> str:
        """The address goes to the queue even when the file failed, as in PHP. See PHP_DEFECTS.md."""
        name = php_uniqid_more_entropy(f"email_file_{php_string(provider_id)}") + ".pdf"
        url = _FILES_URL + name
        try:
            pdf = await html_to_pdf(html)
            await asyncio.to_thread(_write_file, Path(self.public_dir + url), pdf)
        except (PdfError, OSError) as exc:
            logger.error("provider {id}: PDF not written: {error}", id=provider_id, error=exc)
        return url


def _write_file(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
