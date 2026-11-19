"""How PHP's new session per request planned its queries."""

from asyncpg import Connection


async def plan_like_a_new_php_session(connection: Connection) -> None:
    """PHP opened a session per request, so a query got custom plans for its first five runs.

    A pooled session stays on a generic plan once it has moved to one, and a function without
    ORDER BY then returns its rows in another order: ``rest.ddu_request_info`` gave BI Group
    the other request of a deal. Not exact: PHP does go generic on a sixth run within a
    request. See contour/compare/RESULTS.md. Ends with the transaction.
    """
    await connection.fetchval("select set_config('plan_cache_mode', 'force_custom_plan', true)")
