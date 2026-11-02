"""A function refusing on purpose is an ordinary answer; only a real failure is logged as error."""

from collections.abc import Iterator

import pytest
from asyncpg.exceptions import RaiseError, UndefinedFunctionError
from httpx import AsyncClient
from loguru import logger

from tests.big_integration.test_contract import FakeConnection, _auth, client, db

__all__ = ("client", "db")

URL = "/api/big_integration/request-info"
FUNCTION = "rest.ddu_request_info"


@pytest.fixture
def log_levels() -> Iterator[list[str]]:
    levels: list[str] = []
    sink = logger.add(
        lambda message: levels.append(message.record["level"].name),
        filter=lambda record: "database error" in record["message"],
    )
    yield levels
    logger.remove(sink)


async def test_refusal_of_a_function_is_logged_at_info(
    client: AsyncClient,
    db: FakeConnection,
    log_levels: list[str],
) -> None:
    db.responses = {FUNCTION: RaiseError("{Заявка не найдена}")}

    response = await client.get(URL, headers=_auth())

    assert response.status_code == 500
    assert log_levels == ["INFO"]


async def test_unhandled_database_error_is_logged_at_error(
    client: AsyncClient,
    db: FakeConnection,
    log_levels: list[str],
) -> None:
    db.responses = {FUNCTION: UndefinedFunctionError("function does not exist")}

    response = await client.get(URL, headers=_auth())

    assert response.status_code == 500
    assert log_levels == ["ERROR"]
