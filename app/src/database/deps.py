from collections.abc import AsyncGenerator
from typing import Annotated

from asyncpg import Connection, Pool
from fastapi import Depends

from .pool import get_db_pool


async def get_db_connection(
    db_pool: Annotated[Pool, Depends(get_db_pool)],
) -> AsyncGenerator[Connection]:
    async with db_pool.acquire() as connection, connection.transaction():
        yield connection


DatabaseConnectionDep = Annotated[
    Connection,
    Depends(get_db_connection),
]
