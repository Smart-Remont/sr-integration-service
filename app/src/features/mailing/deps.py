from collections.abc import AsyncGenerator
from typing import Annotated

from asyncpg import Connection, Pool
from fastapi import Depends

from src.config import mailing_config
from src.database.pool import get_db_pool
from src.shared.cron_run import held_run_connection

from .letter_queue import MailingQueueService
from .repo import MailingRepository
from .service import MailingFunctionsService
from .smtp import SmtpTransport


async def get_mailing_connection(
    db_pool: Annotated[Pool, Depends(get_db_pool)],
) -> AsyncGenerator[Connection]:
    """No transaction stays open: each step commits on its own while the mail server waits."""
    async with held_run_connection(db_pool, cron_name="Mailing") as connection:
        yield connection


# function scope: the connection, and with it the lock, goes back before the response is sent.
_Connection = Annotated[Connection, Depends(get_mailing_connection, scope="function")]


def get_smtp_transport_class() -> type[SmtpTransport]:
    return SmtpTransport


def get_queue_service(
    connection: _Connection,
    transport_class: Annotated[type[SmtpTransport], Depends(get_smtp_transport_class)],
) -> MailingQueueService:
    return MailingQueueService(
        repository=MailingRepository(connection=connection),
        config=mailing_config,
        transport_factory=transport_class,
    )


def get_functions_service(connection: _Connection) -> MailingFunctionsService:
    return MailingFunctionsService(repository=MailingRepository(connection=connection))


MailingQueueServiceDep = Annotated[MailingQueueService, Depends(get_queue_service)]
MailingFunctionsServiceDep = Annotated[MailingFunctionsService, Depends(get_functions_service)]
