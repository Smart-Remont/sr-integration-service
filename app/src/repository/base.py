import re
import uuid
from collections.abc import Awaitable, Callable, Sequence
from typing import Self

from asyncpg import Connection, Record

from src.database.pool import get_db_pool
from src.exceptions import map_asyncpg_errors

type SpRow = dict[str, object]
type SpRows = list[SpRow]

_FUNCTION_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*(\.[a-zA-Z_][a-zA-Z0-9_]*)*$")
_CAST_TYPE_RE = re.compile(r"^[a-z][a-z ]*$")


def scalar_from_sp_rows(rows: SpRows) -> object | None:
    """First column of the first row: the return value of a scalar stored function."""
    if not rows:
        return None
    return next(iter(rows[0].values()))


def _valid_cast_type(cast: str) -> str:
    if not _CAST_TYPE_RE.fullmatch(cast):
        raise ValueError(f"Invalid cast type: {cast}")
    return cast


class BaseRepository:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    async def committed[T](self, action: Callable[[Self], Awaitable[T]]) -> T:
        """Run ``action`` on its own pooled connection and commit it at once.

        The request transaction rolls back on any HTTP error. Use this only for
        writes that must survive that rollback: an audit/event log entry, or a
        state change the external system already knows about (bank, MyNCA).
        """
        pool = await get_db_pool()
        async with pool.acquire() as connection, connection.transaction():
            return await action(type(self)(connection=connection))

    async def fetchrow(self, query: str, *args: object) -> Record | None:
        with map_asyncpg_errors():
            return await self.connection.fetchrow(query, *args)

    async def fetch(self, query: str, *args: object) -> list[Record]:
        with map_asyncpg_errors():
            return await self.connection.fetch(query, *args)

    async def execute(self, query: str, *args: object) -> str:
        with map_asyncpg_errors():
            return await self.connection.execute(query, *args)

    async def call_sp(
        self,
        method: str,
        *args: object,
        cursor: bool = False,
        session_user_id: int | None = None,
        module_code: str = "MYSPACE",
        text_casts: Sequence[str | None] = (),
    ) -> SpRows:
        """
        Call a PostgreSQL stored function.

        - Pass arguments directly: call_sp("public.fn", id)
        - A function that opens a refcursor as its first argument: cursor=True
        - Strings the server must convert itself, with its own error text, as PHP sent them:
          text_casts=("integer", None), one type per argument, None for no conversion
        """
        self._validate_function_name(method)

        cursor_name = f"cur_{uuid.uuid4().hex[:8]}" if cursor else None
        bound_args = [cursor_name, *args] if cursor_name else list(args)
        casts = [None, *text_casts] if cursor_name else list(text_casts)
        query = self._build_select_function_sql(method, len(bound_args), casts)

        with map_asyncpg_errors():
            await self._set_db_session(session_user_id, module_code)

            if cursor_name is None:
                rows = await self.connection.fetch(query, *bound_args)
                return self._rows_to_dicts(rows)

            await self.connection.execute(query, *bound_args)
            return await self._fetch_refcursor(cursor_name)

    async def _set_db_session(
        self,
        session_user_id: int | None,
        module_code: str,
    ) -> None:
        # Transaction-local (is_local = true): pooled connections are reused by other
        # requests, so a session-level value would leak this user into their audit trail.
        # Calls normally run inside a transaction (DatabaseConnectionDep / committed()). Outside one
        # (the SMS send-run lock) the value ends with this statement and does not reach the function.
        if session_user_id is not None:
            await self.connection.fetchval(
                "select set_config('myapp.user_id', $1, true)",
                str(session_user_id),
            )
        await self.connection.fetchval(
            "select set_config('myapp.module_code', $1, true)",
            module_code,
        )

    async def _fetch_refcursor(self, cursor_name: str) -> SpRows:
        rows = await self.connection.fetch(f'fetch all in "{cursor_name}"')
        return self._rows_to_dicts(rows)

    @staticmethod
    def _build_select_function_sql(
        method: str,
        args_count: int,
        casts: Sequence[str | None] = (),
    ) -> str:
        if args_count == 0:
            return f"select {method}()"
        all_casts = [*casts, *[None] * (args_count - len(casts))]
        placeholders = ", ".join(
            f"${index}" if cast is None else f"${index}::text::{_valid_cast_type(cast)}"
            for index, cast in enumerate(all_casts, start=1)
        )
        return f"select {method}({placeholders})"

    @staticmethod
    def _validate_function_name(method: str) -> None:
        if not _FUNCTION_NAME_RE.fullmatch(method):
            raise ValueError(f"Invalid stored function name: {method}")

    @staticmethod
    def _rows_to_dicts(rows: Sequence[Record]) -> SpRows:
        return [dict(row) for row in rows]
