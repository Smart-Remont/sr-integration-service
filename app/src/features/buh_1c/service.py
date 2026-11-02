"""PHP ``RestController::buhAction`` for the values of ``what`` that are alive.

The order of the calls is that of PHP. The journal ``1c_json_tab`` is the first thing an update
writes and nobody looks at its result: its failure changes nothing for the caller, and a later
failure does not take it back. The file logs of PHP are not repeated, the bodies stay in the journal.
A failure goes to the log by its state only: the text of the database can quote the body.
"""

from collections.abc import Awaitable, Callable

from fastapi import Response
from loguru import logger

from src.php_compat.pdo_errors import is_php_unhandled_error

from .errors import (
    Buh1cFunctionError,
    action_response,
    function_error_response,
    not_moved_response,
    value_response,
)
from .repo import Buh1cDatabase, Buh1cRepository
from .whats import NOT_MOVED_WHAT, MovedWhat

type _Update = Callable[[Buh1cRepository, str], Awaitable[str | None]]

_UPDATES: dict[str, _Update] = {
    MovedWhat.LOT_GUID: Buh1cRepository.buh_lot_upd,
    MovedWhat.MATERIAL_GUID: Buh1cRepository.buh_material_upd,
    MovedWhat.WAREHOUSE_GUID: Buh1cRepository.buh_warehouse_upd,
}


class Buh1cService:
    def __init__(self, database: Buh1cDatabase) -> None:
        self.database = database

    async def exchange(self, *, what: str, raw_body: bytes) -> Response:
        if what in NOT_MOVED_WHAT:
            return not_moved_response(what)
        try:
            await self.database.step(Buh1cRepository.get_buh_url)
        except Buh1cFunctionError as exc:
            _log_refusal("rest.get_buh_url", exc)
            await self._write_journal(exc.php_result_json(), what)
            return function_error_response(exc)
        if what == MovedWhat.LOT:
            return await self._read_lots()
        update = _UPDATES.get(what)
        if update is None:
            return action_response()
        return await self._update(update, what, raw_body)

    async def _read_lots(self) -> Response:
        try:
            value = await self.database.step(Buh1cRepository.buh_lot_read)
        except Buh1cFunctionError as exc:
            _log_refusal(MovedWhat.LOT, exc)
            await self._write_journal(None, MovedWhat.LOT)
            return function_error_response(exc)
        await self._write_journal(value, MovedWhat.LOT)
        return value_response(value)

    async def _update(self, update: _Update, what: str, raw_body: bytes) -> Response:
        # surrogateescape: invalid UTF-8 has to reach the check PDO made, with the bytes in its text.
        json_body = raw_body.decode("utf-8", "surrogateescape")
        await self._write_journal(json_body, what)
        try:
            value = await self.database.step(lambda repository: update(repository, json_body))
        except Buh1cFunctionError as exc:
            _log_refusal(what, exc)
            return function_error_response(exc)
        return value_response(value)

    async def _write_journal(self, json_body: str | None, what: str) -> None:
        try:
            await self.database.step(
                lambda repository: repository.insert_1c_json(json_body, what),
            )
        except Buh1cFunctionError as exc:
            logger.error("rest/buh: journal not written, state {state}", state=exc.state)


def _log_refusal(step: str, exc: Buh1cFunctionError) -> None:
    # ``step`` is a name from this package, never the ``what`` of the request.
    level = "ERROR" if is_php_unhandled_error(exc.text) else "INFO"
    logger.log(level, "rest/buh: {step} refused, state {state}", step=step, state=exc.state)
