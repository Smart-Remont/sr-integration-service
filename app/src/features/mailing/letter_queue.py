"""``send-email-new`` and ``send-email-error-db``: the queues of letters.

Same as PHP: the order of calls, a connection for a run, the links, one letter per address, a failure
recorded on the row and printed in the answer.

**The one deliberate difference, the record comes first.** PHP sends and then writes ``is_send = 1``,
and does not look at the result of the write: the next run sends the letter again. Here the row is
marked before the letter goes and committed, so whatever happens to the database after it, the letter
does not go twice. A failed mark means no letter and the end of the run: the database is the problem,
and the row stays in the queue. A refused letter then gets ``is_send = 2``, as in PHP.

What this costs: a process killed between the mark and the end of the sending leaves one row marked as
sent while the letter did not go. The log shows such a row: ``marked`` without ``sent``. The order is
chosen by the rule of the task: better a lost notification than a repeated one.

**Another difference, a server without a reply in time ends the run.** PHP goes on, a late reply then
answers the next command, and each next row gets ``is_send = 2`` with a text of another reply. So the
rest of the queue is lost in PHP. Here the current row is recorded as refused, the others stay queued.

No addresses or texts go to the log.
"""

import asyncio
from collections.abc import Sequence
from pathlib import Path

from fastapi import Response
from loguru import logger

from src.config import MailingConfig
from src.php_compat.pdo_errors import php_error_state, php_pdo_message
from src.php_compat.text import php_trim
from src.repository.base import SpRow

from .message import ComposedLetter, MailAttachment, MailCompositionError, compose_letter
from .repo import MailingRepository
from .responses import cron_response
from .smtp import SmtpError, SmtpSettings, SmtpTimeoutError, SmtpTransport
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
ERROR_QUEUE_LOCK = "send-email-error-db"


class _RunStoppedError(Exception):
    """The mail server stopped answering: the rest of the queue waits for the next run."""


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
        output = bytearray()
        rows = await self.steps.value_or(
            "read_send_email", [], lambda repository: repository.read_send_email()
        )
        transport = self.transport_factory(self._notify_settings())
        try:
            for row in rows:
                await self._send_row(row, transport, output)
        except DatabaseGoneError:
            logger.error("send-email-new stopped: the database is gone")
        except _RunStoppedError:
            logger.error("send-email-new stopped: the mail server does not answer")
        finally:
            await transport.close()
        return cron_response(bytes(output))

    async def send_error_queue(self) -> Response:
        if not await self.steps.take_lock(ERROR_QUEUE_LOCK):
            return cron_response()
        output = bytearray()
        try:
            rows = await self.steps.write(
                "read_send_error", lambda repository: repository.read_send_error()
            )
        except DatabaseGoneError:
            raise
        except Exception as exc:
            # PHP prints the error of its read and stops.
            logger.error("read_send_error failed: {error}", error=php_error_state(exc))
            output += _pdo_text(exc)
            return cron_response(bytes(output))
        transport = self.transport_factory(self._notify_settings())
        try:
            for row in rows:
                transport = await self._send_error_row(row, transport, output)
        except DatabaseGoneError:
            logger.error("send-email-error-db stopped: the database is gone")
        except _RunStoppedError:
            logger.error("send-email-error-db stopped: the mail server does not answer")
        finally:
            await transport.close()
        return cron_response(bytes(output))

    def _notify_settings(self) -> SmtpSettings:
        config = self.config
        return SmtpSettings(
            config.smtp_host, config.smtp_port, config.smtp_username, config.smtp_password
        )

    async def _send_row(self, row: SpRow, transport: SmtpTransport, output: bytearray) -> None:
        send_email_id = row["send_email_id"]
        attachments = await self.steps.value_or(
            "read_send_email_attachment",
            [],
            lambda repository: repository.read_send_email_attachment(send_email_id),
        )
        for email in _text(row.get("email")).split(";"):
            await self._send_to_address(row, email, attachments, transport, output)

    async def _send_to_address(
        self,
        row: SpRow,
        email: str,
        attachments: Sequence[SpRow],
        transport: SmtpTransport,
        output: bytearray,
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
        await self._deliver_notification(
            row, email, html, attachments, transport, unsubscribe_url, output
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

    async def _deliver_notification(
        self,
        row: SpRow,
        email: str,
        html: str,
        attachments: Sequence[SpRow],
        transport: SmtpTransport,
        unsubscribe_url: str,
        output: bytearray,
    ) -> None:
        send_email_id = row["send_email_id"]
        address = php_trim(email)
        config = self.config
        carbon_copy = None
        if row.get("what") == _EXPEDITOR_CODE_ACCEPT:
            carbon_copy = (config.expeditor_cc_address, config.expeditor_cc_name)
        files = [await self._read_attachment(item) for item in attachments]
        try:
            letter = compose_letter(
                from_address=config.smtp_username,
                from_name=config.from_name,
                to=[address],
                carbon_copy=carbon_copy,
                subject=_optional_text(row.get("email_title")),
                html=html,
                attachments=files,
                extra_headers=[("Precedence", "list"), ("List-Unsubscribe", unsubscribe_url)],
            )
        except MailCompositionError as exc:
            await self._record_refusal(send_email_id, address, str(exc), 0, output)
            return
        await self._mark_sent(send_email_id)
        try:
            await transport.send(letter)
        except SmtpError as exc:
            await self._record_refusal(send_email_id, address, exc.message, exc.code, output)
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

    async def _send_error_row(
        self,
        row: SpRow,
        transport: SmtpTransport,
        output: bytearray,
    ) -> SmtpTransport:
        """A failed letter of this queue switches to the mailbox the letters go from, as in PHP."""
        send_error_id = row["send_error_id"]
        config = self.config
        addressees = [
            php_trim(piece) for piece in _text(row.get("email")).split(";") if php_trim(piece) != ""
        ]
        letter = _composed_error_letter(row, addressees, config)
        await self._mark_error_sent(send_error_id)
        try:
            if isinstance(letter, str):
                raise SmtpError(letter)
            await transport.send(letter)
        except SmtpError as exc:
            message = exc.message
            output += message.encode("utf-8", "replace")
            await self.steps.value_or(
                "upd_error_send_failed",
                None,
                lambda repository: repository.upd_error_send_failed(send_error_id, message),
            )
            if isinstance(exc, SmtpTimeoutError):
                raise _RunStoppedError from exc
            await transport.close()
            return self.transport_factory(
                SmtpSettings(
                    config.smtp_host,
                    config.smtp_port,
                    config.error_from_address,
                    config.error_smtp_password,
                )
            )
        logger.info("send_error {id}: sent", id=send_error_id)
        return transport

    async def _mark_error_sent(self, send_error_id: object) -> None:
        try:
            await self.steps.write(
                "upd_error_send",
                lambda repository: repository.upd_error_send(send_error_id),
            )
            logger.info("send_error {id}: marked", id=send_error_id)
        except DatabaseGoneError:
            raise
        except Exception as exc:
            logger.error(
                "upd_error_send {id} failed, letter not sent, run stopped: {error}",
                id=send_error_id,
                error=type(exc).__name__,
            )
            raise DatabaseGoneError("upd_error_send") from exc


def _composed_error_letter(
    row: SpRow, addressees: Sequence[str], config: MailingConfig
) -> ComposedLetter | str:
    """A letter, or the text of the failure when it cannot be composed."""
    try:
        return compose_letter(
            from_address=config.error_from_address,
            from_name=config.error_from_name,
            to=addressees,
            subject=_optional_text(row.get("email_title")),
            html=_text(row.get("email_content")),
        )
    except MailCompositionError as exc:
        return str(exc)


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


def _pdo_text(exc: Exception) -> bytes:
    return php_pdo_message(exc).encode("utf-8", "surrogateescape")
