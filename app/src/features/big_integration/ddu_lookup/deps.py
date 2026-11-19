from typing import Annotated

from fastapi import Depends

from src.database.deps import DatabaseConnectionDep

from .repo import DduLookupRepository
from .service import DduLookupService


def get_ddu_lookup_service(connection: DatabaseConnectionDep) -> DduLookupService:
    return DduLookupService(repository=DduLookupRepository(connection=connection))


DduLookupServiceDep = Annotated[DduLookupService, Depends(get_ddu_lookup_service)]
