from typing import Annotated

from fastapi import Depends

from src.config import app_config
from src.database.deps import DatabaseConnectionDep

from .ff.client import FFClient
from .ff.repo import FFRepository
from .ff.service import FFService


def get_ff_service(connection: DatabaseConnectionDep) -> FFService:
    ff_repository = FFRepository(connection=connection)
    ff_client = FFClient()
    return FFService(
        ff_repository=ff_repository,
        ff_client=ff_client,
        app_env=app_config.env,
    )


FFServiceDep = Annotated[FFService, Depends(get_ff_service)]
