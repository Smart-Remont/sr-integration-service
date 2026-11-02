"""The crons ``fill-send-email`` and ``paid-client-request-to-remont``: one function, one call."""

from asyncpg import InterfaceError
from fastapi import Response
from loguru import logger

from src.php_compat.json_codec import php_json_encode
from src.php_compat.pdo_errors import php_clean_error, php_error_state, php_pdo_message
from src.shared.cron_run import database_step_limit

from .repo import MailingRepository
from .responses import cron_response, json_response

# The functions walk many requests, and neither PHP nor a cron limits them. Below the 8-minute period.
_LONG_STEP_SECONDS = 420.0
_NOTICE_SEPARATOR = "@"
FILL_LOCK = "fill-send-email"
PAID_LOCK = "paid-client-request-to-remont"


class MailingFunctionsService:
    def __init__(self, repository: MailingRepository) -> None:
        self.repository = repository

    async def fill_send_email(self) -> Response:
        """PHP also adds a row to ``send_error_tab`` on a failure: nothing reads that table."""
        if not await self._take_lock(FILL_LOCK):
            return cron_response()
        try:
            async with (
                database_step_limit(_LONG_STEP_SECONDS),
                self.repository.transaction(),
            ):
                await self.repository.fill_send_email()
        except InterfaceError, OSError, TimeoutError:
            raise
        except Exception as exc:
            logger.error("fill_send_email failed: {error}", error=php_error_state(exc))
            return cron_response(php_pdo_message(exc).encode("utf-8", "surrogateescape"))
        return cron_response()

    async def paid_client_request_to_remont(self) -> Response:
        if not await self._take_lock(PAID_LOCK):
            return json_response(_failure_json("Предыдущий запуск ещё идёт", None))
        async with database_step_limit():
            await self.repository.clear_session_notice()
        try:
            async with (
                database_step_limit(_LONG_STEP_SECONDS),
                self.repository.transaction(),
            ):
                value = await self.repository.change_paid_client_request_to_remont()
        except InterfaceError, OSError, TimeoutError:
            raise
        except Exception as exc:
            logger.error(
                "change_paid_client_request_to_remont failed: {error}",
                error=php_error_state(exc),
            )
            pdo_message = php_pdo_message(exc)
            return json_response(_failure_json(php_clean_error(pdo_message), pdo_message))
        notices = await self._session_notices()
        return json_response(
            php_json_encode({"value": value, "status": True, "notice": notices}),
        )

    async def _take_lock(self, cron_name: str) -> bool:
        async with database_step_limit():
            locked = await self.repository.try_lock_run(cron_name)
        if not locked:
            logger.warning("{cron} skipped: the previous run is not finished", cron=cron_name)
        return locked

    async def _session_notices(self) -> list[str]:
        """``get_session_notice``: the text splits on @ and the part after the last @ goes away."""
        async with database_step_limit():
            text = await self.repository.get_session_notice()
            await self.repository.clear_session_notice()
        return text.split(_NOTICE_SEPARATOR)[:-1] if text else []


def _failure_json(error: str, debug: str | None) -> bytes:
    """``execSP`` fills these in this order when the call fails."""
    return php_json_encode(
        {"value": None, "notice": [], "error": error, "error_debug": debug, "status": False}
    )
