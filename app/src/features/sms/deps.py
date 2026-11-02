from collections.abc import AsyncGenerator
from typing import Annotated

from asyncpg import Connection, Pool
from fastapi import Depends

from src.config import kcell_config
from src.database.pool import get_db_pool
from src.shared.cron_run import held_run_connection

from .client import KcellClient
from .repo import SmsRepository
from .service import SmsService


async def get_sms_connection(
    db_pool: Annotated[Pool, Depends(get_db_pool)],
) -> AsyncGenerator[Connection]:
    """No transaction stays open: the service commits each step on its own while Kcell waits."""
    async with held_run_connection(db_pool, cron_name="SMS") as connection:
        yield connection


def get_kcell_client() -> KcellClient:
    return KcellClient(kcell_config)


def get_sms_service(
    # function scope: the connection, and with it the lock, goes back before the response is sent.
    connection: Annotated[Connection, Depends(get_sms_connection, scope="function")],
    kcell: Annotated[KcellClient, Depends(get_kcell_client)],
) -> SmsService:
    return SmsService(repository=SmsRepository(connection=connection), kcell=kcell)


SmsServiceDep = Annotated[SmsService, Depends(get_sms_service)]
