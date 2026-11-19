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

# The exit code of a default dependency runs after the response is sent, so a failed COMMIT stays
# unseen by the caller. Here the commit comes first: "paid" never goes out for a lost payment.
CommitBeforeResponseConnectionDep = Annotated[
    Connection,
    Depends(get_db_connection, scope="function"),
]
