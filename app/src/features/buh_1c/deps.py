from typing import Annotated

from asyncpg import Pool
from fastapi import Depends

from src.database.pool import get_db_pool

from .repo import Buh1cDatabase
from .service import Buh1cService


def get_buh_1c_service(pool: Annotated[Pool, Depends(get_db_pool)]) -> Buh1cService:
    return Buh1cService(database=Buh1cDatabase(pool))


Buh1cServiceDep = Annotated[Buh1cService, Depends(get_buh_1c_service)]
