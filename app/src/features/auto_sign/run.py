"""How each of these crons starts. PHP ``return 0`` when the lock is taken and on a failed read."""

from collections.abc import Awaitable, Callable

from loguru import logger

from src.php_compat.pdo_errors import php_error_state
from src.repository.base import SpRows
from src.shared.cron_run import database_step_limit

from .repo import AutoSignRepository


async def read_rows_under_run_lock(
    repository: AutoSignRepository,
    run_name: str,
    read_rows: Callable[[AutoSignRepository], Awaitable[SpRows]],
) -> SpRows:
    """No rows when the previous run is not finished or the read fails: the run ends quietly."""
    # Cron fires again while a run goes on, and wget repeats after its timeout while the service still
    # works on the first request. Without the lock both runs would take the same rows.
    try:
        async with database_step_limit():
            locked = await repository.try_lock_run(run_name)
        if not locked:
            logger.warning("Cron run skipped, the previous one goes on | run={run}", run=run_name)
            return []
        async with database_step_limit(), repository.transaction():
            rows = await read_rows(repository)
    except Exception as exc:
        # Class or SQLSTATE only, here and in these crons: database texts can quote an IIN.
        logger.error(
            "Cron read failed | run={run} error={error}",
            run=run_name,
            error=php_error_state(exc),
        )
        return []
    logger.info("Cron run started | run={run} rows={rows}", run=run_name, rows=len(rows))
    return rows
