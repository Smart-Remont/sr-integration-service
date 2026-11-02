"""PHP ``pdfFindSumAction``: is the sum of a contractor agreement written in its signed PDF."""

import asyncio
import re
from collections.abc import Awaitable, Callable

import pymupdf
from fastapi import Response
from loguru import logger

from src.php_compat.pdo_errors import php_error_state
from src.php_compat.text import php_float
from src.repository.base import SpRow
from src.shared.cron_run import database_step_limit

from .repo import AutoSignRepository
from .responses import empty_cron_response
from .run import read_rows_under_run_lock

_RUN_NAME = "auto-sign:pdf-find-sum"
_PDF_SIGNATURE = b"%PDF-"
# PHP had no limit, and this file sits in memory of the worker that also signs.
MAX_PDF_BYTES = 50 * 1024 * 1024
# Parsing runs in a thread, which cannot be stopped: at the limit the run stops waiting for it.
_PARSE_SECONDS = 60.0

type ReadRemoteFile = Callable[..., Awaitable[bytes | None]]


class AgreementSumService:
    def __init__(
        self,
        repository: AutoSignRepository,
        read_remote_file: ReadRemoteFile,
        partner_url: str,
    ) -> None:
        self._repository = repository
        self._read_remote_file = read_remote_file
        self._partner_url = partner_url

    async def check_agreement_sums(self) -> Response:
        rows = await read_rows_under_run_lock(
            self._repository,
            _RUN_NAME,
            AutoSignRepository.parse_pdf__read_agreement,
        )
        for row in rows:
            await self._check_agreement(row)
        return empty_cron_response()

    async def _check_agreement(self, row: SpRow) -> None:
        sign_id = int(str(row["sign_id"]))
        try:
            is_ok = await self._sum_is_in_document(sign_id, row.get("agreement_sum"))
        except Exception as exc:
            logger.warning(
                "PDF sum check failed | sign_id={sign_id} error={error}",
                sign_id=sign_id,
                error=php_error_state(exc),
            )
            is_ok = None
        try:
            async with database_step_limit(), self._repository.transaction():
                await self._repository.parse_pdf__process_agreement(sign_id=sign_id, is_ok=is_ok)
        except Exception as exc:
            # PHP does not look at this result either.
            logger.error(
                "PDF sum result not recorded | sign_id={sign_id} error={error}",
                sign_id=sign_id,
                error=php_error_state(exc),
            )

    async def _sum_is_in_document(self, sign_id: int, agreement_sum: object) -> bool | None:
        url = f"{self._partner_url}/contractor_agreement_list/signed/{sign_id}/"
        content = await self._read_remote_file(url, max_bytes=MAX_PDF_BYTES)
        if content is None or not content.startswith(_PDF_SIGNATURE):
            return None
        async with asyncio.timeout(_PARSE_SECONDS):
            text = await asyncio.to_thread(_pdf_text, content)
        return sum_is_in_text(None if agreement_sum is None else str(agreement_sum), text)


def sum_is_in_text(agreement_sum: str | None, text: str) -> bool:
    """The sum, give or take 1, with any whitespace between its digits.

    ``re.ASCII`` because PCRE without ``/u`` reads bytes: a non-breaking space does not separate digits
    and a Cyrillic letter next to a digit is a word boundary. Only spaces are removed before the
    comparison, as in PHP: a number split by a line break reads as its first part.
    """
    amount = int(agreement_sum) if agreement_sum else 0
    lower = amount - 1
    upper = amount + 1
    variants = "|".join(
        _digits_pattern(number) for number in (str(lower), agreement_sum, str(upper))
    )
    pattern = re.compile(rf"\b({variants})(?:\.\d+)?\b", re.ASCII)
    return any(
        lower <= php_float(match.group().replace(" ", "")) <= upper
        for match in pattern.finditer(text)
    )


def _digits_pattern(number: str | None) -> str:
    return r"\s*".join(number or "")


def _pdf_text(content: bytes) -> str:
    with pymupdf.open(stream=content, filetype="pdf") as document:
        return "\n".join(page.get_text() for page in document)
