from src.php_compat.pdo_errors import php_error_text
from src.php_compat.pdo_values import libpq_c_string, pg_row
from src.repository import BaseRepository
from src.repository.base import SpRow, SpRows

from .errors import BigIntegrationDatabaseError

DDU_MODULE_CODE = "DDU"


class BigIntegrationSpReader(BaseRepository):
    """Stored-function reads with PHP values and PHP error texts."""

    _custom_plans_set = False

    async def _plan_like_a_new_session(self) -> None:
        """PHP opened a session per request, so a query got custom plans for its first five runs.

        A pooled session stays on a generic plan once it has moved to one, and a function without
        ORDER BY then returns its rows in another order: ``rest.ddu_request_info`` gave BI Group
        the other request of a deal. Not exact: PHP does go generic on a sixth run within a
        request. See contour/compare/RESULTS.md.
        """
        if self._custom_plans_set:
            return
        await self.connection.fetchval(
            "select set_config('plan_cache_mode', 'force_custom_plan', true)",
        )
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


def php_database_error(exc: BaseException) -> BigIntegrationDatabaseError:
    return BigIntegrationDatabaseError(php_error_text(exc))
