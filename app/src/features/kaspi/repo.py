import hashlib
from collections.abc import Sequence

from asyncpg.transaction import Transaction

from src.repository import BaseRepository
from src.repository.base import SpRow, SpRows, scalar_from_sp_rows
from src.shared.php_db_errors import php_invalid_encoding_message
from src.shared.php_json import pg_row

from .errors import KaspiDatabaseError
from .params import KaspiLogEntry

# PHP sets no module for these calls, and the functions compare the value with fixed names.
_MODULE_CODE = ""
_INT = "integer"
_FLOAT = "double precision"
# ``type`` of client.kaspi_check and client.insert_kaspi_pay_log: account is a client request.
_ACCOUNT_IS_REQUEST = "1"
_LOCK_KEY_PREFIX = "kaspi:"


class KaspiRepository(BaseRepository):
    """Every parameter goes as text, as PDO sent it, so the server gives PHP's error texts."""

    def savepoint(self) -> Transaction:
        return self.connection.transaction()

    async def lock_account(self, account: str) -> None:
        """Held until the transaction ends: a retry waits and then sees the first payment."""
        await self.call_sp(
            "pg_catalog.pg_advisory_xact_lock",
            account_lock_key(account),
            module_code=_MODULE_CODE,
        )

    async def kaspi_check(
        self,
        *,
        client_request_id: str,
        iin: str,
        command: str,
        amount: str,
        txn_id: str,
    ) -> int | None:
        rows = await self._call(
            "client.kaspi_check",
            (client_request_id, None, iin, command, _ACCOUNT_IS_REQUEST, amount, txn_id),
            (_INT, _INT, None, None, _INT, _FLOAT, None),
        )
        return scalar_from_sp_rows(rows)

    async def kaspi_request_get_info(self, client_request_id: str, iin: str) -> SpRow | None:
        rows = await self._call(
            "client.kaspi_request_get_info",
            (client_request_id, iin),
            (_INT, None),
            cursor=True,
        )
        return pg_row(rows[0]) if rows else None

    async def kaspi_request_pay(
        self,
        *,
        client_request_id: str,
        iin: str,
        amount: str,
        txn_id: str,
        txn_date: str,
    ) -> int | None:
        rows = await self._call(
            "client.kaspi_request_pay",
            (client_request_id, iin, amount, txn_id, txn_date),
            (_INT, None, _FLOAT, None, None),
        )
        return scalar_from_sp_rows(rows)

    async def after_payment_operations(self, payment_id: int | None) -> None:
        text_id = None if payment_id is None else str(payment_id)
        await self._call("public.after_payment_operations", (text_id,), (_INT,))

    async def insert_kaspi_pay_log(self, entry: KaspiLogEntry) -> None:
        await self._call(
            "client.insert_kaspi_pay_log",
            (
                entry.command,
                entry.request_text,
                entry.response_xml.decode("utf-8", "surrogateescape"),
                entry.account,
                entry.txn_id,
                entry.amount,
                entry.txn_date,
                _ACCOUNT_IS_REQUEST,
                str(entry.status),
            ),
            (None, None, None, _INT, None, _FLOAT, None, _INT, _INT),
        )

    async def _call(
        self,
        function: str,
        args: Sequence[str | None],
        casts: Sequence[str | None],
        *,
        cursor: bool = False,
    ) -> SpRows:
        bound_args = [_php_string(arg) for arg in args]
        try:
            return await self.call_sp(
                function,
                *bound_args,
                cursor=cursor,
                module_code=_MODULE_CODE,
                text_casts=casts,
            )
        except Exception as exc:
            raise KaspiDatabaseError.from_exception(exc) from exc


def account_lock_key(account: str) -> int:
    """The signed 64-bit key of ``pg_advisory_xact_lock``."""
    text = f"{_LOCK_KEY_PREFIX}{account}".encode("utf-8", "surrogateescape")
    return int.from_bytes(hashlib.sha256(text).digest()[:8], "big", signed=True)


def _php_string(value: str | None) -> str | None:
    if value is None:
        return None
    # libpq takes a C string: the value ends at the first NUL byte.
    text = value.partition("\0")[0]
    message = php_invalid_encoding_message(text)
    if message:
        raise KaspiDatabaseError(message)
    return text
