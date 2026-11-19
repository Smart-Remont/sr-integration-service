from typing import Annotated

from asyncpg import Pool
from fastapi import Depends

from src.config import showroom_config
from src.database.pool import get_db_pool

from .client import ShowroomClient
from .repo import ShowroomDatabase
from .service import ShowroomService


def get_showroom_client() -> ShowroomClient:
    return ShowroomClient(showroom_config.bi_url)


def get_showroom_service(
    pool: Annotated[Pool, Depends(get_db_pool)],
    client: Annotated[ShowroomClient, Depends(get_showroom_client)],
) -> ShowroomService:
    return ShowroomService(database=ShowroomDatabase(pool), client=client)


ShowroomServiceDep = Annotated[ShowroomService, Depends(get_showroom_service)]
