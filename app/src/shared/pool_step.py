"""One database step on a connection from the pool, for routes that wait for partners between steps.

The transaction ends with the step, so no connection is held while a partner answers, and the commit
comes before the caller answers. What a failure means is the business of each route: how the database
answers there decides the answer of the route.
"""

from collections.abc import Awaitable, Callable

from asyncpg import Connection, Pool

from .cron_run import database_step_limit


def _keep_the_error(_: Exception) -> Exception | None:
    return None


async def pool_step[T](
    pool: Pool,
    action: Callable[[Connection], Awaitable[T]],
    *,
    prepare: Callable[[Connection], Awaitable[None]] | None = None,
    to_error: Callable[[Exception], Exception | None] = _keep_the_error,
) -> T:
    """``to_error`` sees a failure inside the transaction and its commit; None raises it as it is.

    A failed ``acquire`` and the time limit go past it to the caller, as ``TimeoutError`` for the limit.
    """
    async with database_step_limit(), pool.acquire() as connection:
        try:
            async with connection.transaction():
                if prepare is not None:
                    await prepare(connection)
                return await action(connection)
        except Exception as exc:
            error = to_error(exc)
            if error is None:
                raise
            raise error from exc
