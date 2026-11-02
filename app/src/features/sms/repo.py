import hashlib

from asyncpg.transaction import Transaction

from src.repository import BaseRepository
from src.repository.base import SpRows, scalar_from_sp_rows
from src.shared.php_json import pg_row

# PHP sets no module for these calls.
_MODULE_CODE = ""
_SEND_RUN_LOCK_KEY = int.from_bytes(
    hashlib.sha256(b"sms:sms-notify").digest()[:8],
    "big",
    signed=True,
)


class SmsRepository(BaseRepository):
    """The connection stays outside a transaction while Kcell answers."""

    def transaction(self) -> Transaction:
        return self.connection.transaction()

    async def try_lock_send_run(self) -> bool:
        """A session lock. It ends with the session or when the pool takes the connection back."""
        rows = await self.call_sp(
            "pg_catalog.pg_try_advisory_lock",
            _SEND_RUN_LOCK_KEY,
            module_code=_MODULE_CODE,
        )
        return scalar_from_sp_rows(rows) is True

    async def sms_notify__read(self) -> SpRows:
        rows = await self.call_sp("notify.sms_notify__read", cursor=True, module_code=_MODULE_CODE)
        return [pg_row(row) for row in rows]

    async def sms_notify_result__set(
        self,
        *,
        ids: list[int],
        batch_id: str | None,
        created_at: str | None,
        response: str,
        results: str | None,
    ) -> None:
        await self.call_sp(
            "notify.sms_notify_result__set",
            ids,
            batch_id,
            created_at,
            response,
            results,
            module_code=_MODULE_CODE,
        )

    async def sms_notify_batch__read(self) -> SpRows:
        rows = await self.call_sp(
            "notify.sms_notify_batch__read",
            cursor=True,
            module_code=_MODULE_CODE,
        )
        return [pg_row(row) for row in rows]

    async def sms_notify_status__set(self, messages: str) -> None:
        await self.call_sp("notify.sms_notify_status__set", messages, module_code=_MODULE_CODE)
