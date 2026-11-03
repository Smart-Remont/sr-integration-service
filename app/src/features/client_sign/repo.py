from collections.abc import Awaitable, Callable, Sequence

from asyncpg import InterfaceError, Pool, PostgresError

from src.php_compat.pdo_values import NO_MODULE_CODE, pdo_bound_text, pdo_text, pg_row
from src.php_compat.text import zero_to_null
from src.repository import BaseRepository
from src.repository.base import SpRow, SpRows, scalar_from_sp_rows
from src.shared.advisory_lock import advisory_lock_key
from src.shared.pool_step import pool_step
from src.shared.sign_tab import (
    SignTabCall,
    id_by_process_call,
    insert_sign_general_call,
    sign_tab_modify_call,
)

from .doc_types import DocumentType
from .errors import ClientSignDatabaseError

_INT = "integer"
_LOCK_KEY_PREFIX = "client-sign:"
_INVALID_ENCODING_STATE = "22021"
_DOCUMENT_FUNCTIONS = {
    DocumentType.DS: "ds.ds_get_by_uuid",
    DocumentType.OTBASY_STATEMENT: "public.otbasy_statement_get_by_uuid",
    DocumentType.AGREEMENT: "public.client_request_get_by_uuid",
}


class ClientSignRepository(BaseRepository):
    """Every parameter goes as text, as PDO sent it, so the server gives PHP's error texts."""

    async def client_request_id_by_hash(self, request_hash: str) -> int | None:
        rows = await self._call("client.sign_client_request_id_get", (request_hash,), (None,))
        value = scalar_from_sp_rows(rows)
        return value if isinstance(value, int) else None

    async def client_info(self, client_request_id: int | None) -> SpRow | None:
        rows = await self._read_cursor("public.get_client_info", client_request_id)
        return rows[0] if rows else None

    async def request_documents(self, client_request_id: int | None) -> SpRows:
        return await self._read_cursor("client.sign_request_doc_read", client_request_id)

    async def deleted_signatures(self, client_request_id: int | None) -> SpRows:
        return await self._read_cursor("client.sign_deleted_read", client_request_id)

    async def document_by_uuid(self, doc_type: DocumentType, uuid: str) -> SpRow | None:
        # zeroToNull: the functions answer "Документ не найден" to NULL.
        rows = await self._call(
            _DOCUMENT_FUNCTIONS[doc_type], (zero_to_null(uuid),), (None,), cursor=True
        )
        return pg_row(rows[0]) if rows else None

    async def lock_sign_process(self, sign_process_id: str) -> None:
        """Held until the transaction ends: a repeated callback waits and then sees the first one."""
        try:
            await self.call_sp(
                "pg_catalog.pg_advisory_xact_lock",
                sign_process_lock_key(sign_process_id),
                module_code=NO_MODULE_CODE,
            )
        except Exception as exc:
            raise ClientSignDatabaseError.from_exception(exc) from exc

    async def sign_by_process(self, sign_process_id: str) -> SpRow | None:
        rows = await self._run(id_by_process_call(sign_process_id), cursor=True)
        return pg_row(rows[0]) if rows else None

    async def insert_sign_general(
        self,
        *,
        client_request_id: int | None,
        ds_id: int | None,
        client_request_document_id: int | None,
        sign_type_code: str,
        dn_name: str,
    ) -> object | None:
        rows = await self._run(
            insert_sign_general_call(
                client_request_id=client_request_id,
                ds_id=ds_id,
                client_request_document_id=client_request_document_id,
                sign_type_code=sign_type_code,
                dn_name=dn_name,
                source="MYNCA",
                sign_pid=None,
            )
        )
        return scalar_from_sp_rows(rows)

    async def attach_sign_process(
        self,
        *,
        sign_id: object | None,
        sign_process_id: str,
        sign_group_id: str | None,
    ) -> None:
        await self._run(
            sign_tab_modify_call(
                sign_id=sign_id,
                sign_process_id=sign_process_id,
                sign_group_id=sign_group_id,
            )
        )

    async def _run(self, call: SignTabCall, *, cursor: bool = False) -> SpRows:
        return await self._call(call.function, call.args, call.casts, cursor=cursor)

    async def _read_cursor(self, function: str, client_request_id: int | None) -> SpRows:
        rows = await self._call(function, (pdo_text(client_request_id),), (_INT,), cursor=True)
        return [pg_row(row) for row in rows]

    async def _call(
        self,
        function: str,
        args: Sequence[str | None],
        casts: Sequence[str | None],
        *,
        cursor: bool = False,
    ) -> SpRows:
        bound_args = [pdo_bound_text(arg, _invalid_encoding_error) for arg in args]
        try:
            return await self.call_sp(
                function,
                *bound_args,
                cursor=cursor,
                module_code=NO_MODULE_CODE,
                text_casts=casts,
            )
        except Exception as exc:
            raise ClientSignDatabaseError.from_exception(exc) from exc


class ClientSignDatabase:
    """One short transaction per step, as in PHP. No connection is held while a partner answers."""

    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def step[T](self, action: Callable[[ClientSignRepository], Awaitable[T]]) -> T:
        """The commit comes before the caller answers, so a lost commit is never reported as done.

        The limit covers all of it, also the wait of a repeated callback in ``lock_sign_process``.
        """
        try:
            return await pool_step(
                self._pool,
                lambda connection: action(ClientSignRepository(connection=connection)),
            )
        except (PostgresError, InterfaceError, OSError, TimeoutError) as exc:
            raise ClientSignDatabaseError.from_exception(exc) from exc


def sign_process_lock_key(sign_process_id: str) -> int:
    """The signed 64-bit key of ``pg_advisory_xact_lock``."""
    return advisory_lock_key(f"{_LOCK_KEY_PREFIX}{sign_process_id}")


def _invalid_encoding_error(pdo_message: str) -> ClientSignDatabaseError:
    return ClientSignDatabaseError(pdo_message, _INVALID_ENCODING_STATE)
