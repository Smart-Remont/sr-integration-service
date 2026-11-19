"""What crons moved from PHP share: a time limit per database step, one held connection, the run lock,
the stop signal.

A run keeps its connection, and with it the session advisory lock, from start to end. No transaction
stays open on it while a partner answers: each database step commits on its own.
"""

import asyncio
import signal
import threading
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from types import FrameType
from typing import Annotated, Any

from asyncpg import Connection, Pool
from asyncpg.transaction import Transaction
from fastapi import Depends, HTTPException, status
from loguru import logger

from src.database.pool import get_db_pool
from src.php_compat.pdo_values import NO_MODULE_CODE
from src.repository import BaseRepository, scalar_from_sp_rows

from .advisory_lock import advisory_lock_key

DB_STEP_SECONDS = 60.0
# At the limit asyncpg asks the server to cancel the query, and a transaction then rolls back. On an
# unreachable server that rollback would wait for the cancel forever, and this margin ends the wait.
ROLLBACK_MARGIN_SECONDS = 10.0
_RELEASE_SECONDS = DB_STEP_SECONDS
_LOCK_FUNCTION = "pg_catalog.pg_try_advisory_lock"
_stop_requested = False


def install_run_stop_signal() -> None:
    """Called from the lifespan: uvicorn has already set its SIGTERM handler, and it keeps working.

    uvicorn says nothing to a running request: it waits for the request until gunicorn kills the worker
    at ``graceful_timeout``, and the lifespan shuts down only after that. So the signal itself has to
    reach a cron run, for the run to end on its own between rows instead of being killed in the middle
    of one. A newer uvicorn that restores its handler would only lose this stop, as before it.
    """
    if threading.current_thread() is not threading.main_thread():
        return
    uvicorn_handler = signal.getsignal(signal.SIGTERM)

    def handle_stop(number: int, frame: FrameType | None) -> None:
        global _stop_requested
        _stop_requested = True
        if callable(uvicorn_handler):
            uvicorn_handler(number, frame)

    signal.signal(signal.SIGTERM, handle_stop)


def run_stop_requested() -> bool:
    """Checked between the rows of a run: the rest stays for the next run, of the next process."""
    return _stop_requested


@asynccontextmanager
async def database_step_limit(seconds: float | None = None) -> AsyncIterator[None]:
    """A hung database call must not stop a cron forever. Going over the limit: ``TimeoutError``."""
    limit = DB_STEP_SECONDS if seconds is None else seconds
    async with (
        asyncio.timeout(limit + ROLLBACK_MARGIN_SECONDS),
        asyncio.timeout(limit),
    ):
        yield


@asynccontextmanager
async def held_run_connection(db_pool: Pool, *, cron_name: str) -> AsyncIterator[Connection]:
    """The connection of a whole run. No free connection in time: 503."""
    try:
        async with database_step_limit():
            connection = await db_pool.acquire()
    except TimeoutError as exc:
        logger.error(
            "{cron_name} step acquire failed: {error}",
            cron_name=cron_name,
            error=type(exc).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Нет соединения с БД",
        ) from exc
    try:
        yield connection
    finally:
        await _release_connection(db_pool, connection, cron_name)


async def _release_connection(db_pool: Pool, connection: Connection, cron_name: str) -> None:
    # asyncpg's own limit, not database_step_limit: the release is shielded from cancellation, and
    # only this limit closes a stuck connection. Closing ends its session and the run lock.
    try:
        await db_pool.release(connection, timeout=_RELEASE_SECONDS)
    except Exception as exc:
        logger.error(
            "{cron_name} step release failed: {error}",
            cron_name=cron_name,
            error=type(exc).__name__,
        )


def run_connection_dep(cron_name: str) -> Any:
    """The connection of a run as a dependency. No transaction stays open on it.

    Function scope: the connection, and with it the lock, goes back before the response is sent.
    """

    async def get_run_connection(
        db_pool: Annotated[Pool, Depends(get_db_pool)],
    ) -> AsyncGenerator[Connection]:
        async with held_run_connection(db_pool, cron_name=cron_name) as connection:
            yield connection

    return Annotated[Connection, Depends(get_run_connection, scope="function")]


class CronRepository(BaseRepository):
    """On the connection of a run: it stays outside a transaction while a partner answers."""

    def transaction(self) -> Transaction:
        return self.connection.transaction()

    @asynccontextmanager
    async def step(self, seconds: float | None = None) -> AsyncIterator[None]:
        """One database step: its own transaction, committed at the end, with a time limit."""
        async with database_step_limit(seconds), self.transaction():
            yield

    async def take_run_lock(self, run_name: str) -> bool:
        """A session lock. It ends with the session or when the pool takes the connection back."""
        async with database_step_limit():
            rows = await self.call_sp(
                _LOCK_FUNCTION,
                advisory_lock_key(run_name),
                module_code=NO_MODULE_CODE,
            )
        return scalar_from_sp_rows(rows) is True
