import re

from asyncpg.exceptions import PostgresError

from src.repository import BaseRepository
from src.repository.base import SpRow, SpRows
from src.shared.php_json import pg_row

from .errors import BigIntegrationDatabaseError

DDU_MODULE_CODE = "DDU"

_RAISED_TEXT = re.compile(r"\{[^@]*\}")
_WORD_START = re.compile(r"(?<!^)(?=[A-Z])")
# PDO names the state by its standard text; for these the asyncpg class name is not the same.
_STATE_NAMES = {"P0001": "Raise exception"}


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
    """``_getErrorClean``: the raised ``{text}``, otherwise the PDO text behind a fixed prefix."""
    cause = _postgres_error(exc)
    text = str(cause or exc)
    raised = _RAISED_TEXT.search(text)
    if raised:
        return BigIntegrationDatabaseError(raised.group().replace("{", "").replace("}", ""))
    return BigIntegrationDatabaseError(php_unhandled_error_text(cause, text))


def php_unhandled_error_text(cause: PostgresError | None, text: str) -> str:
    if cause is None:
        return f"Произошла необработанная ошибка в БД {text}"
    details = (
        ("DETAIL", cause.detail),
        ("HINT", cause.hint),
        ("CONTEXT", cause.context),
    )
    # libpq adds these lines to the message, and PDO passes them on.
    full_text = text + "".join(f"\n{name}:  {value}" for name, value in details if value)
    return php_pdo_error_text(cause.sqlstate, _state_name(cause), full_text)


def php_pdo_error_text(sqlstate: str, state_name: str, text: str) -> str:
    return (
        f"Произошла необработанная ошибка в БД SQLSTATE[{sqlstate}]: {state_name}: 7 ERROR:  {text}"
    )


def _state_name(error: PostgresError) -> str:
    if error.sqlstate in _STATE_NAMES:
        return _STATE_NAMES[error.sqlstate]
    words = _WORD_START.sub(" ", type(error).__name__.removesuffix("Error"))
    return words.capitalize()


def _postgres_error(exc: BaseException) -> PostgresError | None:
    cause: BaseException | None = exc
    while cause is not None:
        if isinstance(cause, PostgresError):
            return cause
        cause = cause.__cause__
    return None
