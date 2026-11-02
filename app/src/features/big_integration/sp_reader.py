from collections.abc import Sequence

from src.php_compat.pdo_errors import php_error_text
from src.php_compat.pdo_values import libpq_c_string, pg_row
from src.php_compat.session_plans import plan_like_a_new_php_session
from src.repository import BaseRepository, scalar_from_sp_rows
from src.repository.base import SpRow, SpRows

from .errors import BigIntegrationDatabaseError

DDU_MODULE_CODE = "DDU"
# PHP sets no module for the calls of ``IntegrationController``.
NO_MODULE_CODE = ""


class BigIntegrationSpReader(BaseRepository):
    """Stored-function reads with PHP values and PHP error texts."""

    _custom_plans_set = False

    async def _plan_like_a_new_session(self) -> None:
        if self._custom_plans_set:
            return
        await plan_like_a_new_php_session(self.connection)
        self._custom_plans_set = True

    async def read_rows(self, function: str, *args: object) -> SpRows:
        try:
            await self._plan_like_a_new_session()
            rows = await self.call_sp(
                function,
                *[libpq_c_string(arg) if isinstance(arg, str) else arg for arg in args],
                cursor=True,
                module_code=DDU_MODULE_CODE,
            )
        except Exception as exc:
            raise php_database_error(exc) from exc
        return [pg_row(row) for row in rows]

    async def read_row(self, function: str, *args: object) -> SpRow | None:
        rows = await self.read_rows(function, *args)
        return rows[0] if rows else None

    async def read_scalar(
        self,
        function: str,
        argument: str | None,
        *,
        module_code: str,
        text_casts: Sequence[str | None] = (),
    ) -> object | None:
        """``scalarSP`` and ``execSP``: the first column of the first row."""
        try:
            await self._plan_like_a_new_session()
            rows = await self.call_sp(
                function,
                None if argument is None else libpq_c_string(argument),
                module_code=module_code,
                text_casts=text_casts,
            )
        except Exception as exc:
            raise php_database_error(exc) from exc
        return scalar_from_sp_rows(rows)


def php_database_error(exc: BaseException) -> BigIntegrationDatabaseError:
    return BigIntegrationDatabaseError(php_error_text(exc))
