from typing import Annotated

from fastapi import Depends

from src.database.deps import CommitBeforeResponseConnectionDep

from .repo import KaspiRepository
from .service import KaspiService


def get_kaspi_service(connection: CommitBeforeResponseConnectionDep) -> KaspiService:
    return KaspiService(repository=KaspiRepository(connection=connection))


KaspiServiceDep = Annotated[KaspiService, Depends(get_kaspi_service)]
