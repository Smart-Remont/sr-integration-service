from typing import Annotated

from fastapi import Depends

from src.config import mailing_config
from src.shared.cron_run import run_connection_dep

from .letter_queue import MailingQueueService
from .repo import MailingRepository
from .service import MailingFunctionsService
from .smtp import SmtpTransport

_Connection = run_connection_dep("Mailing")


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
