"""What crons moved from PHP share: a time limit per database step and one held connection.

A run keeps its connection, and with it the session advisory lock, from start to end. No transaction
stays open on it while a partner answers: each database step commits on its own.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from asyncpg import Connection, Pool
from fastapi import HTTPException, status
from loguru import logger

DB_STEP_SECONDS = 60.0
# At the limit asyncpg asks the server to cancel the query, and a transaction then rolls back. On an
# unreachable server that rollback would wait for the cancel forever, and this margin ends the wait.
ROLLBACK_MARGIN_SECONDS = 10.0
_RELEASE_SECONDS = DB_STEP_SECONDS


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
