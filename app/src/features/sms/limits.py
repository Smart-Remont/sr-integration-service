import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

DB_STEP_SECONDS = 60.0
# At the limit asyncpg asks the server to cancel the query, and a transaction then rolls back. On an
# unreachable server that rollback would wait for the cancel forever, and this margin ends the wait.
ROLLBACK_MARGIN_SECONDS = 10.0


@asynccontextmanager
async def database_step_limit() -> AsyncIterator[None]:
    """A hung database call must not stop the SMS crons. Going over the limit: ``TimeoutError``."""
    async with (
        asyncio.timeout(DB_STEP_SECONDS + ROLLBACK_MARGIN_SECONDS),
        asyncio.timeout(DB_STEP_SECONDS),
    ):
        yield
