from collections.abc import Awaitable, Callable

from asyncpg import Pool, PostgresError

from src.exceptions.infra import InfrastructureError
from src.php_compat.pdo_errors import php_error_state, php_pdo_message
from src.php_compat.pdo_values import pdo_bound_text, pdo_text
from src.php_compat.session_plans import plan_like_a_new_php_session
from src.repository import BaseRepository, scalar_from_sp_rows

from .errors import Buh1cFunctionError

# PHP sets no module for the calls of ``RestController``.
_MODULE_CODE = ""
_INVALID_ENCODING_STATE = "22021"
# The database is gone, as opposed to a function that failed: connection errors, shutdown.
_NO_DATABASE_STATE_CLASS = "08"
_NO_DATABASE_STATES = frozenset({"57P01", "57P02", "57P03"})


def _invalid_encoding_error(pdo_message: str) -> Buh1cFunctionError:
    return Buh1cFunctionError(pdo_message, state=_INVALID_ENCODING_STATE)


class Buh1cRepository(BaseRepository):
    async def get_buh_url(self) -> None:
        await self.call_sp("rest.get_buh_url", module_code=_MODULE_CODE)

    async def insert_1c_json(self, json_body: str | None, what: str) -> None:
        await self.call_sp(
            "public.insert_1c_json",
            pdo_bound_text(json_body, _invalid_encoding_error),
            pdo_bound_text(what, _invalid_encoding_error),
            module_code=_MODULE_CODE,
        )

    async def buh_lot_read(self) -> str | None:
        return await self._value("rest.buh_lot_read")

    async def buh_lot_upd(self, json_text: str) -> str | None:
        return await self._value("rest.buh_lot_upd", json_text)

    async def buh_material_upd(self, json_text: str) -> str | None:
        return await self._value("rest.buh_material_upd", json_text)

    async def buh_warehouse_upd(self, json_text: str) -> str | None:
        return await self._value("rest.buh_warehouse_upd", json_text)

    async def _value(self, function: str, *json_text: str) -> str | None:
        arguments = [pdo_bound_text(text, _invalid_encoding_error) for text in json_text]
        rows = await self.call_sp(function, *arguments, module_code=_MODULE_CODE)
        return pdo_text(scalar_from_sp_rows(rows))


class Buh1cDatabase:
    """One transaction per function, as PHP ran them: the journal stays when the update fails."""

    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def step[T](self, action: Callable[[Buh1cRepository], Awaitable[T]]) -> T:
        """A commit comes before the answer.

        Without a database the error goes further and the caller gets 500: PHP redirects to
        ``/dict/permission`` there, which is no answer for 1C either. Errors of ``acquire`` are this
        case too, even those of the ``PostgresError`` family.
        """
        async with self._pool.acquire() as connection:
            try:
                async with connection.transaction():
                    await plan_like_a_new_php_session(connection)
                    return await action(Buh1cRepository(connection=connection))
            except (PostgresError, InfrastructureError) as exc:
                state = php_error_state(exc)
                if state.startswith(_NO_DATABASE_STATE_CLASS) or state in _NO_DATABASE_STATES:
                    raise
                raise Buh1cFunctionError(php_pdo_message(exc), state=state) from exc
