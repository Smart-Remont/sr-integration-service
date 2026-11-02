"""Functions of the crons. Each commits on its own, with a time limit, as PHP calls them."""

from asyncpg.transaction import Transaction

from src.php_compat.pdo_values import pg_row
from src.repository import BaseRepository
from src.repository.base import SpRow, SpRows, scalar_from_sp_rows
from src.shared.advisory_lock import advisory_lock_key

# PHP sets no module for these calls.
_MODULE_CODE = ""
_LOCK_FUNCTION = "pg_catalog.pg_try_advisory_lock"
_SET_CONFIG_FUNCTION = "pg_catalog.set_config"
_NOTICE_SETTING = "myapp.notice"
STATE_SENT = 1
STATE_FAILED = 2


class MailingRepository(BaseRepository):
    """The connection stays outside a transaction while the mail server answers."""

    def transaction(self) -> Transaction:
        return self.connection.transaction()

    async def try_lock_run(self, cron_name: str) -> bool:
        """A session lock. It ends with the session or when the pool takes the connection back."""
        rows = await self.call_sp(
            _LOCK_FUNCTION,
            advisory_lock_key(f"mailing:{cron_name}"),
            module_code=_MODULE_CODE,
        )
        return scalar_from_sp_rows(rows) is True

    async def _read(self, function: str, *args: object) -> SpRows:
        rows = await self.call_sp(function, *args, cursor=True, module_code=_MODULE_CODE)
        return [pg_row(row) for row in rows]

    async def _scalar(self, function: str, *args: object) -> object | None:
        rows = await self.call_sp(function, *args, module_code=_MODULE_CODE)
        return scalar_from_sp_rows(rows)

    async def fill_send_email(self) -> None:
        await self._scalar("public.fill_send_email")

    async def read_send_email(self) -> SpRows:
        return await self._read("public.read_send_email")

    async def read_send_email_attachment(self, send_email_id: object) -> SpRows:
        return await self._read("public.read_send_email_attachment", send_email_id)

    async def email_check_employee_subscribe(self, email: str) -> object | None:
        return await self._scalar("public.email_check_employee_subscribe", email)

    async def email_get_unsubscribe_info(self, email: str) -> SpRow | None:
        rows = await self._read("public.email_get_unsubscribe_info", email)
        return rows[0] if rows else None

    async def email_get_remont_token(
        self, employee_id: object, client_id: object, send_email_id: object
    ) -> object | None:
        return await self._scalar(
            "public.email_get_remont_token", employee_id, client_id, send_email_id
        )

    async def email_unsubscribe_hard(self, email: str) -> None:
        await self._scalar("public.email_unsubscribe_hard", email)

    async def upd_email_send(self, send_email_id: object) -> None:
        await self._scalar("public.upd_email_send", send_email_id, STATE_SENT)

    async def upd_email_send_failed(self, send_email_id: object, error: str) -> None:
        await self._scalar("public.upd_email_send_failed", send_email_id, STATE_FAILED, error)

    async def change_paid_client_request_to_remont(self) -> object | None:
        return await self._scalar("public.change_paid_client_request_to_remont")

    async def get_session_notice(self) -> str:
        """A session setting. In PHP the session lives one request, here the pool keeps it."""
        value = await self._scalar("public.get_session_notice")
        return "" if value is None else str(value)

    async def clear_session_notice(self) -> None:
        await self.call_sp(
            _SET_CONFIG_FUNCTION, _NOTICE_SETTING, "", False, module_code=_MODULE_CODE
        )
