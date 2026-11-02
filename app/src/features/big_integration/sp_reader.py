from src.php_compat.pdo_errors import php_error_text
from src.php_compat.pdo_values import pg_row
from src.repository import BaseRepository
from src.repository.base import SpRow, SpRows

from .errors import BigIntegrationDatabaseError

DDU_MODULE_CODE = "DDU"


class BigIntegrationSpReader(BaseRepository):
    """Stored-function reads with PHP values and PHP error texts."""

    async def read_rows(self, function: str, *args: object) -> SpRows:
        try:
            rows = await self.call_sp(function, *args, cursor=True, module_code=DDU_MODULE_CODE)
        except Exception as exc:
            raise php_database_error(exc) from exc
        return [pg_row(row) for row in rows]

    async def read_row(self, function: str, *args: object) -> SpRow | None:
        rows = await self.read_rows(function, *args)
        return rows[0] if rows else None


def php_database_error(exc: BaseException) -> BigIntegrationDatabaseError:
    return BigIntegrationDatabaseError(php_error_text(exc))
