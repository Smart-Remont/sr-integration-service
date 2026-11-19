from typing import Annotated

from fastapi import Depends

from src.database.deps import DatabaseConnectionDep

from .repo import GetConstructivesRepository
from .service import GetConstructivesService


def get_get_constructives_service(connection: DatabaseConnectionDep) -> GetConstructivesService:
    return GetConstructivesService(repository=GetConstructivesRepository(connection=connection))


GetConstructivesServiceDep = Annotated[
    GetConstructivesService,
    Depends(get_get_constructives_service),
]
