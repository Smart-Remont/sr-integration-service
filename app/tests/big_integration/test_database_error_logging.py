"""A function refusing on purpose is an ordinary answer; only a real failure is logged as error."""

from collections.abc import Iterator

import pytest
from asyncpg.exceptions import RaiseError, UndefinedFunctionError
from httpx import AsyncClient
from loguru import logger

from tests.big_integration.fakes import FakeConnection, _auth

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


@pytest.fixture
def log_lines() -> Iterator[list[str]]:
    lines: list[str] = []
    sink = logger.add(lines.append, format="{message}", level="DEBUG")
    yield lines
    logger.remove(sink)


async def test_global_handler_logs_the_state_and_not_the_text(
    client: AsyncClient,
    db: FakeConnection,
    log_lines: list[str],
) -> None:
    db.responses = {FUNCTION: UndefinedFunctionError("текст с 77001234567")}

    response = await client.get(URL, headers=_auth())

    assert response.status_code == 500
    logged = "".join(log_lines)
    assert "77001234567" not in logged
    assert "state 42883" in logged


async def test_big_notify_client_logs_the_state_and_not_the_text(
    client: AsyncClient,
    db: FakeConnection,
    log_lines: list[str],
) -> None:
    db.responses = {"rest.big_notify_client": RaiseError("{текст с 77001234567}")}

    response = await client.post(
        "/api/big_integration/big-notify-client", content=b"{}", headers=_auth()
    )

    assert response.status_code == 400
    assert "77001234567" in response.text
    logged = "".join(log_lines)
    assert "77001234567" not in logged
    assert "state P0001" in logged
