from typing import Annotated

from fastapi import Depends

from src.database.deps import DatabaseConnectionDep

from .repo import RequestCreateV3Repository
from .service import RequestCreateV3Service


def get_request_create_v3_service(
    connection: DatabaseConnectionDep,
) -> RequestCreateV3Service:
    repository = RequestCreateV3Repository(connection=connection)
    return RequestCreateV3Service(repository=repository)


RequestCreateV3ServiceDep = Annotated[
    RequestCreateV3Service,
    Depends(get_request_create_v3_service),
]
