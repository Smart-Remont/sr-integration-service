from typing import Annotated

from fastapi import Depends

from src.config import kcell_config
from src.shared.cron_run import run_connection_dep

from .client import KcellClient
from .repo import SmsRepository
from .service import SmsService

_Connection = run_connection_dep("SMS")


def get_kcell_client() -> KcellClient:
    return KcellClient(kcell_config)


def get_sms_service(
    connection: _Connection,
    kcell: Annotated[KcellClient, Depends(get_kcell_client)],
) -> SmsService:
    return SmsService(repository=SmsRepository(connection=connection), kcell=kcell)


SmsServiceDep = Annotated[SmsService, Depends(get_sms_service)]
