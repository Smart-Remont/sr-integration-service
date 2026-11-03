from collections.abc import Awaitable, Callable, Sequence

from asyncpg import InterfaceError, Pool, PostgresError

from src.php_compat.pdo_values import NO_MODULE_CODE, pdo_bound_text, pdo_text, pg_row
from src.php_compat.text import zero_to_null
from src.repository import BaseRepository
from src.repository.base import SpRow, SpRows, scalar_from_sp_rows
from src.shared.advisory_lock import advisory_lock_key
from src.shared.cron_run import database_step_limit

from .doc_types import DocumentType
from .errors import ClientSignDatabaseError

_INT = "integer"
_UUID = "uuid"
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
        rows = await self._call(
            "client.sign_tab__id_by_process", (sign_process_id,), (_UUID,), cursor=True
        )
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
        rows = await self._call(
            "client.insert_sign_general",
            (
                pdo_text(client_request_id),
                pdo_text(ds_id),
                pdo_text(client_request_document_id),
                sign_type_code,
                # The signed document itself stays in MyNCA, sign_tab keeps a marker.
                "SIGN_CMS",
                "SIGN_CMS",
                None,
                dn_name,
                "MYNCA",
                None,
            ),
            (_INT, _INT, _INT, None, None, None, None, None, None, _INT),
        )
        return scalar_from_sp_rows(rows)

    async def attach_sign_process(
        self,
        *,
        sign_id: object | None,
        sign_process_id: str,
        sign_group_id: str | None,
    ) -> None:
        await self._call(
            "client.sign_tab__modify",
            (pdo_text(sign_id), sign_process_id, sign_group_id, "CMS", None),
            (_INT, _UUID, _UUID, None, None),
        )

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
            async with (
                database_step_limit(),
                self._pool.acquire() as connection,
                connection.transaction(),
            ):
                return await action(ClientSignRepository(connection=connection))
        except (PostgresError, InterfaceError, OSError, TimeoutError) as exc:
            raise ClientSignDatabaseError.from_exception(exc) from exc


def sign_process_lock_key(sign_process_id: str) -> int:
    """The signed 64-bit key of ``pg_advisory_xact_lock``."""
    return advisory_lock_key(f"{_LOCK_KEY_PREFIX}{sign_process_id}")


def _invalid_encoding_error(pdo_message: str) -> ClientSignDatabaseError:
    return ClientSignDatabaseError(pdo_message, _INVALID_ENCODING_STATE)
