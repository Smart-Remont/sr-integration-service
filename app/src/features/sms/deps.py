from collections.abc import AsyncGenerator
from typing import Annotated

from asyncpg import Connection, Pool
from fastapi import Depends, HTTPException, status
from loguru import logger

from src.config import kcell_config
from src.database.pool import get_db_pool

from .client import KcellClient
from .limits import DB_STEP_SECONDS, database_step_limit
from .repo import SmsRepository
from .service import SmsService


async def get_sms_connection(
    db_pool: Annotated[Pool, Depends(get_db_pool)],
) -> AsyncGenerator[Connection]:
    """No transaction stays open: the service commits each step on its own while Kcell waits."""
    try:
        async with database_step_limit():
            connection = await db_pool.acquire()
    except TimeoutError as exc:
        logger.error("SMS step acquire failed: {error}", error=type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Нет соединения с БД",
        ) from exc
    try:
        yield connection
    finally:
        await _release_connection(db_pool, connection)


async def _release_connection(db_pool: Pool, connection: Connection) -> None:
    # asyncpg's own limit, not database_step_limit: the release is shielded from cancellation, and
    # only this limit closes a stuck connection. Closing ends its session and the send-run lock.
    try:
        await db_pool.release(connection, timeout=DB_STEP_SECONDS)
    except Exception as exc:
        logger.error("SMS step release failed: {error}", error=type(exc).__name__)


def get_kcell_client() -> KcellClient:
    return KcellClient(kcell_config)


def get_sms_service(
    # function scope: the connection, and with it the lock, goes back before the response is sent.
    connection: Annotated[Connection, Depends(get_sms_connection, scope="function")],
    kcell: Annotated[KcellClient, Depends(get_kcell_client)],
) -> SmsService:
    return SmsService(repository=SmsRepository(connection=connection), kcell=kcell)


SmsServiceDep = Annotated[SmsService, Depends(get_sms_service)]
