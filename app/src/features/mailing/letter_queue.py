"""``send-email-new``: the queue of letters.

Same as PHP: the order of calls, a connection for a run, the links, one letter per address, a failure
recorded on the row and printed in the answer. Three deliberate differences:

**The record comes first.** PHP sends and then writes ``is_send = 1``, and does not look at the result
of the write: the next run sends the letter again. Here the row is marked before the letter goes and
committed, so whatever happens to the database after it, the letter does not go twice. A failed mark
means no letter and the end of the run: the database is the problem, and the row stays in the queue. A
refused letter then gets ``is_send = 2``, as in PHP.

What this costs: a process killed between the mark and the end of the sending leaves one row marked as
sent while the letter did not go. The log shows such a row: ``marked`` without ``sent``. The order is
chosen by the rule of the task: better a lost notification than a repeated one.

**The session opens before the queue.** In PHP a failure of connecting, TLS or the login gets
``is_send = 2`` on every row of the run, and nothing ever repeats those rows: a wrong password loses the
letters, password resets among them. Here such a failure ends the run with 503 before any row is touched.
So a session opens for a queue that turns out to have no letter to send, where PHP does not connect.

**No reply in time after the session opened ends the run.** PHP goes on, a late reply then answers the
next command, and each next row gets ``is_send = 2`` with a text of another reply. So the rest of the
queue is lost in PHP. Here the current row is recorded as refused, the others stay queued. A connection
lost in the middle goes as in PHP: the remaining rows of the run are recorded as refused.

No addresses or texts go to the log.
"""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from fastapi import Response, status
from loguru import logger

from src.config import MailingConfig
from src.php_compat.text import php_trim
from src.repository.base import SpRow
from src.shared.cron_run import run_stop_requested

from .message import MailAttachment, MailCompositionError, compose_letter
from .repo import MailingRepository
from .responses import cron_response
from .smtp import SmtpError, SmtpSessionError, SmtpSettings, SmtpTimeoutError, SmtpTransport
from .steps import DatabaseGoneError, MailingSteps
from .views import render_letter

_WHAT_RESET = "RESET_PASSWORD"
_EXPEDITOR_CODE_ACCEPT = "EXPEDITOR_CODE_ACCEPT"
_FOLLOW_REMONT_TEXT = "Перейти в ремонт"
_FOLLOW_RESET_TEXT = "Сбросить"
_EMPLOYEE = "EMPLOYEE"
_CLIENT = "CLIENT"
# A refusal with this code means a dead mailbox: PHP takes it off the list for good.
_UNSUBSCRIBE_CODE = 504
QUEUE_LOCK = "send-email-new"
_SESSION_NOT_OPENED = "Mail session not opened, the queue is not touched"


class _RunStoppedError(Exception):
    """The mail server stopped answering: the rest of the queue waits for the next run."""


@dataclass(frozen=True)
class _QueueRun:
    transport: SmtpTransport
    output: bytearray


@dataclass(frozen=True)
class _LetterDraft:
    row: SpRow
    address: str
    html: str
    attachments: Sequence[SpRow]
    unsubscribe_url: str


class MailingQueueService:
    def __init__(
        self,
        repository: MailingRepository,
        config: MailingConfig,
        transport_factory: type[SmtpTransport] = SmtpTransport,
    ) -> None:
        self.steps = MailingSteps(repository)
        self.config = config
        self.transport_factory = transport_factory

    async def send_queue(self) -> Response:
        if not await self.steps.take_lock(QUEUE_LOCK):
            return cron_response()
        rows = await self.steps.value_or(
            "read_send_email", [], lambda repository: repository.read_send_email()
        )
        if not rows:
            return cron_response()
        run = _QueueRun(self.transport_factory(self._smtp_settings()), bytearray())
        try:
            await run.transport.open_session()
            for row in rows:
                if run_stop_requested():
                    logger.warning("send-email-new stopped between rows: the service is stopping")
                    break
                await self._send_row(row, run)
        except SmtpSessionError as exc:
            logger.error(
                "send-email-new stopped: mail session not opened, {count} rows stay queued, "
                "code {code}: {error}",
                count=len(rows),
                code=exc.code,
                error=exc.message,
            )
            return cron_response(
                f"{_SESSION_NOT_OPENED}: {exc.message}".encode("utf-8", "replace"),
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except DatabaseGoneError:
            logger.error("send-email-new stopped: the database is gone")
        except _RunStoppedError:
            logger.error("send-email-new stopped: the mail server does not answer")
        finally:
            await run.transport.close()
        return cron_response(bytes(run.output))

    def _smtp_settings(self) -> SmtpSettings:
        config = self.config
        return SmtpSettings(
            config.smtp_host, config.smtp_port, config.smtp_username, config.smtp_password
        )

    async def _send_row(self, row: SpRow, run: _QueueRun) -> None:
        send_email_id = row["send_email_id"]
        attachments = await self.steps.value_or(
            "read_send_email_attachment",
            [],
            lambda repository: repository.read_send_email_attachment(send_email_id),
        )
        for email in _text(row.get("email")).split(";"):
            await self._send_to_address(row, email, attachments, run)

    async def _send_to_address(
        self,
        row: SpRow,
        email: str,
        attachments: Sequence[SpRow],
        run: _QueueRun,
    ) -> None:
        subscribed = await self.steps.value_or(
            "email_check_employee_subscribe",
            None,
            lambda repository: repository.email_check_employee_subscribe(email),
        )
        if subscribed != 1:
            return
        info = await self.steps.value_or(
            "email_get_unsubscribe_info",
            None,
            lambda repository: repository.email_get_unsubscribe_info(email),
        )
        token = _text((info or {}).get("token"))
        unsubscribe_url = f"{self.config.link_base_url}/index/unsubscribe?token={token}"
        follow_url, follow_text = await self._follow_button(row, info)
        html = render_letter(
            email_content=row.get("email_content"),
            unsubscribe_url=unsubscribe_url,
            employee_fio=(info or {}).get("employee_fio"),
            follow_url=follow_url,
            follow_text=follow_text,
        )
        await self._send_letter(
            _LetterDraft(row, php_trim(email), html, attachments, unsubscribe_url), run
        )

    async def _follow_button(self, row: SpRow, info: SpRow | None) -> tuple[str, str]:
        employee_id = (info or {}).get("employee_id")
        send_email_id = row["send_email_id"]
        kind = (info or {}).get("employee_type")
        if kind not in (_EMPLOYEE, _CLIENT):
            return "", ""
        token = await self.steps.value_or(
            "email_get_remont_token",
            None,
            lambda repository: repository.email_get_remont_token(
                employee_id if kind == _EMPLOYEE else 0,
                0 if kind == _EMPLOYEE else employee_id,
                send_email_id,
            ),
        )
        if not (isinstance(employee_id, int) and employee_id > 0) or _text(token) == "":
            return "", ""
        base = self.config.link_base_url
        if row.get("what") == _WHAT_RESET:
            return (
                f"{base}/system/redirect-email/mode/reset_password"
                f"/email/{send_email_id}/token/{token}",
                _FOLLOW_RESET_TEXT,
            )
        # ``send_email_tab`` has no ``remont_id``: in PHP the part stays empty. See PHP_DEFECTS.md.
        return (
            f"{base}/system/redirect-email/mode/remont/remont_id/{_text(row.get('remont_id'))}"
            f"/email/{send_email_id}/token/{token}",
            _FOLLOW_REMONT_TEXT,
        )

    async def _send_letter(self, draft: _LetterDraft, run: _QueueRun) -> None:
        row = draft.row
        send_email_id = row["send_email_id"]
        address = draft.address
        config = self.config
        carbon_copy = None
        if row.get("what") == _EXPEDITOR_CODE_ACCEPT:
            carbon_copy = (config.expeditor_cc_address, config.expeditor_cc_name)
        files = [await self._read_attachment(item) for item in draft.attachments]
        try:
            letter = compose_letter(
                from_address=config.smtp_username,
                from_name=config.from_name,
                to=address,
                carbon_copy=carbon_copy,
                subject=_optional_text(row.get("email_title")),
                html=draft.html,
                attachments=files,
                extra_headers=[("Precedence", "list"), ("List-Unsubscribe", draft.unsubscribe_url)],
            )
        except MailCompositionError as exc:
            await self._record_refusal(send_email_id, address, str(exc), 0, run.output)
            return
        await self._mark_sent(send_email_id)
        try:
            await run.transport.send(letter)
        except SmtpError as exc:
            await self._record_refusal(send_email_id, address, exc.message, exc.code, run.output)
            if isinstance(exc, SmtpTimeoutError):
                raise _RunStoppedError from exc
            return
        logger.info("send_email {id}: sent", id=send_email_id)

    async def _mark_sent(self, send_email_id: object) -> None:
        try:
            await self.steps.write(
                "upd_email_send",
                lambda repository: repository.upd_email_send(send_email_id),
            )
            logger.info("send_email {id}: marked", id=send_email_id)
        except DatabaseGoneError:
            raise
        except Exception as exc:
            logger.error(
                "upd_email_send {id} failed, letter not sent, run stopped: {error}",
                id=send_email_id,
                error=type(exc).__name__,
            )
            raise DatabaseGoneError("upd_email_send") from exc

    async def _record_refusal(
        self,
        send_email_id: object,
        address: str,
        message: str,
        code: int,
        output: bytearray,
    ) -> None:
        output += message.encode("utf-8", "replace")
        if code == _UNSUBSCRIBE_CODE:
            await self.steps.value_or(
                "email_unsubscribe_hard",
                None,
                lambda repository: repository.email_unsubscribe_hard(address),
            )
        await self.steps.value_or(
            "upd_email_send_failed",
            None,
            lambda repository: repository.upd_email_send_failed(send_email_id, message),
        )

    async def _read_attachment(self, attachment: SpRow) -> MailAttachment:
        return MailAttachment(
            filename=_optional_text(attachment.get("file_name")),
            content=await _read_public_file(
                self.config.office_public_dir, _text(attachment.get("file_url"))
            ),
        )


async def _read_public_file(root: str, file_url: str) -> bytes:
    """``file_get_contents(DOCUMENT_ROOT . file_url)``. No file: an empty attachment, as in PHP.

    On the combat config PDFs go to MinIO only, so PHP finds nothing on the disk. See PHP_DEFECTS.md.
    """
    try:
        return await asyncio.to_thread(_read_inside, root, file_url)
    except OSError as exc:
        logger.warning("attachment not read: {error}", error=type(exc).__name__)
        return b""


def _read_inside(root: str, file_url: str) -> bytes:
    path = Path(root + file_url).resolve()
    if not path.is_relative_to(Path(root).resolve()):
        # PHP would read it. A path in a queue row must not get any file of the server into a letter.
        raise PermissionError("outside the Office folder")
    return path.read_bytes()


def _text(value: object) -> str:
    return "" if value is None else str(value)


def _optional_text(value: object) -> str | None:
    return None if value is None else str(value)
