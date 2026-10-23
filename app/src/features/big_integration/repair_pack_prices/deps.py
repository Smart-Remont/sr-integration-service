from typing import Annotated

from fastapi import Depends

from src.database.deps import DatabaseConnectionDep

from .repo import RepairPackPricesRepository
from .service import RepairPackPricesService


def get_repair_pack_prices_service(
    connection: DatabaseConnectionDep,
) -> RepairPackPricesService:
    repository = RepairPackPricesRepository(connection=connection)
    return RepairPackPricesService(repository=repository)


RepairPackPricesServiceDep = Annotated[
    RepairPackPricesService,
    Depends(get_repair_pack_prices_service),
]
