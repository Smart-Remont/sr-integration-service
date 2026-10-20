from typing import Annotated

from fastapi import Depends

from src.database.deps import DatabaseConnectionDep

from .repo import DduFlatInfoMultipleRepository
from .service import DduFlatInfoMultipleService


def get_ddu_flat_info_multiple_service(
    connection: DatabaseConnectionDep,
) -> DduFlatInfoMultipleService:
    repository = DduFlatInfoMultipleRepository(connection=connection)
    return DduFlatInfoMultipleService(repository=repository)


DduFlatInfoMultipleServiceDep = Annotated[
    DduFlatInfoMultipleService,
    Depends(get_ddu_flat_info_multiple_service),
]
