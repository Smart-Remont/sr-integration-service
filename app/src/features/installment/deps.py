from typing import Annotated

from fastapi import Depends

from src.config import app_config
from src.database.deps import DatabaseConnectionDep

from .bif.client import BIFClient
from .bif.service import BIFService
from .bif.webhook import BIFWebhookService
from .ff.client import FFClient
from .ff.service import FFService
from .oa_service import InstallmentOAService
from .repo import InstallmentRepository


def get_ff_service(connection: DatabaseConnectionDep) -> FFService:
    return FFService(
        ff_repository=InstallmentRepository(connection=connection),
        ff_client=FFClient(),
        app_env=app_config.env,
    )


def get_bif_service(connection: DatabaseConnectionDep) -> BIFService:
    return BIFService(
        repository=InstallmentRepository(connection=connection),
        client=BIFClient(),
        app_env=app_config.env,
    )


def get_bif_webhook_service(connection: DatabaseConnectionDep) -> BIFWebhookService:
    return BIFWebhookService(repository=InstallmentRepository(connection=connection))


def get_oa_service(connection: DatabaseConnectionDep) -> InstallmentOAService:
    return InstallmentOAService(repository=InstallmentRepository(connection=connection))


FFServiceDep = Annotated[FFService, Depends(get_ff_service)]
BIFServiceDep = Annotated[BIFService, Depends(get_bif_service)]
BIFWebhookServiceDep = Annotated[BIFWebhookService, Depends(get_bif_webhook_service)]
InstallmentOAServiceDep = Annotated[InstallmentOAService, Depends(get_oa_service)]
