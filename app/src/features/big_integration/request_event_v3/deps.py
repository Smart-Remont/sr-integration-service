from typing import Annotated

from fastapi import Depends

from src.database.deps import DatabaseConnectionDep

from .repo import RequestEventV3Repository
from .service import RequestEventV3Service


def get_request_event_v3_service(
    connection: DatabaseConnectionDep,
) -> RequestEventV3Service:
    repository = RequestEventV3Repository(connection=connection)
    return RequestEventV3Service(repository=repository)


RequestEventV3ServiceDep = Annotated[
    RequestEventV3Service,
    Depends(get_request_event_v3_service),
]
