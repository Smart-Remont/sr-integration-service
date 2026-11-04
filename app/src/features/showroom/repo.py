from asyncpg import InterfaceError, Pool

from src.php_compat.pdo_errors import php_error_state
from src.php_compat.pdo_values import NO_MODULE_CODE
from src.repository import BaseRepository, scalar_from_sp_rows
from src.shared.pool_step import pool_step


class ShowroomFunctionError(Exception):
    """The function failed, the case when PHP ``scalarSP`` gives a null value."""


class ShowroomRepository(BaseRepository):
    async def read_marketing_remont_list(self) -> str | None:
        rows = await self.call_sp("rest.read_marketing_remont_list", module_code=NO_MODULE_CODE)
        value = scalar_from_sp_rows(rows)
        return None if value is None else str(value)


class ShowroomDatabase:
    """The transaction ends with the read, so no connection is held while BI Group answers."""

    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def read_list(self) -> str | None:
        return await pool_step(
            self._pool,
            lambda connection: ShowroomRepository(connection).read_marketing_remont_list(),
            to_error=_function_error,
        )


def _function_error(exc: Exception) -> ShowroomFunctionError | None:
    # No database or no answer in time. In PHP this is a redirect, and nothing is sent.
    if isinstance(exc, InterfaceError | OSError | TimeoutError):
        return None
    return ShowroomFunctionError(php_error_state(exc))
