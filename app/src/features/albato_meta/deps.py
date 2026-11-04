from typing import Annotated

from fastapi import Depends

from src.database.deps import CommitBeforeResponseConnectionDep

from .repo import AlbatoMetaRepository
from .service import AlbatoMetaService


def get_albato_meta_service(connection: CommitBeforeResponseConnectionDep) -> AlbatoMetaService:
    return AlbatoMetaService(repository=AlbatoMetaRepository(connection=connection))


AlbatoMetaServiceDep = Annotated[AlbatoMetaService, Depends(get_albato_meta_service)]
