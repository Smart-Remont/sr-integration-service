import asyncpg
from loguru import logger

from .config import db_config

_pool: asyncpg.Pool | None = None


async def init_db_pool() -> None:
    """Create the process-wide pool. Called once from the FastAPI lifespan."""
    global _pool
    _pool = await asyncpg.create_pool(
        **db_config.connect_kwargs,
        min_size=db_config.pool_min_size,
        max_size=db_config.pool_max_size,
    )
    logger.info(
        "PostgreSQL pool created (min={min_size}, max={max_size})",
        min_size=db_config.pool_min_size,
        max_size=db_config.pool_max_size,
    )


async def get_db_pool() -> asyncpg.Pool:
    if _pool is None:
        raise ConnectionError("PostgreSQL connection pool is not initialized.")
    return _pool


async def close_db_pool() -> None:
    global _pool
    if _pool is None:
        return
    await _pool.close()
    _pool = None
    logger.info("PostgreSQL pool closed")
