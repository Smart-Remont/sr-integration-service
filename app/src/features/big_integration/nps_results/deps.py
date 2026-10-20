from typing import Annotated

from fastapi import Depends

from src.database.deps import DatabaseConnectionDep

from .repo import NpsResultsRepository
from .service import NpsResultsService


def get_nps_results_service(connection: DatabaseConnectionDep) -> NpsResultsService:
    repository = NpsResultsRepository(connection=connection)
    return NpsResultsService(repository=repository)


NpsResultsServiceDep = Annotated[NpsResultsService, Depends(get_nps_results_service)]
