"""``fill-send-email`` and ``paid-client-request-to-remont``: one function each."""

import json

import pytest
from asyncpg.exceptions import DivisionByZeroError, InterfaceError, RaiseError
from httpx import AsyncClient

from tests.mailing.fakes import FILL, NOTICE, PAID, SET_CONFIG, Database

FILL_ROUTE = "/api/mailing/fill-send-email"
PAID_ROUTE = "/api/mailing/paid-client-request-to-remont"


async def test_fill_answers_empty(client: AsyncClient, db: Database) -> None:
    response = await client.get(FILL_ROUTE)

    assert response.status_code == 200
    assert response.content == b""
    assert response.headers["content-type"] == "application/json; text/html; charset=utf-8"
    assert db.calls_of(FILL) == [()]
    assert db.events[-1] == "commit"


async def test_fill_failure_prints_the_error_with_code_200(
    client: AsyncClient, db: Database
) -> None:
    db.responses[FILL] = DivisionByZeroError("division by zero")

    response = await client.get(FILL_ROUTE)

    assert response.status_code == 200
    assert b"division by zero" in response.content
    assert db.events[-1] == "rollback"


async def test_fill_overlap_does_nothing(client: AsyncClient, db: Database) -> None:
    db.lock_is_held = True

    response = await client.get(FILL_ROUTE)

    assert response.status_code == 200
    assert db.calls_of(FILL) == []


async def test_paid_answers_the_result_of_exec_sp(client: AsyncClient, db: Database) -> None:
    response = await client.get(PAID_ROUTE)

    assert response.status_code == 200
    assert response.content == b'{"value":0,"status":true,"notice":[]}'
    assert response.headers["content-type"] == "application/json"


async def test_paid_notices_split_on_at_and_do_not_stay_on_the_connection(
    client: AsyncClient, db: Database
) -> None:
    db.responses[NOTICE] = [{"id": "Первое@Второе/с косой@"}]

    response = await client.get(PAID_ROUTE)

    assert (
        response.content
        == '{"value":0,"status":true,"notice":["Первое","Второе\\/с косой"]}'.encode()
    )
    assert db.calls_of(SET_CONFIG) == [("myapp.notice", "", False), ("myapp.notice", "", False)]


async def test_paid_failure_is_code_200_with_the_texts_of_php(
    client: AsyncClient, db: Database
) -> None:
    db.responses[PAID] = RaiseError("{Отказ/функции}")

    response = await client.get(PAID_ROUTE)

    assert response.status_code == 200
    answer = json.loads(response.content)
    assert list(answer) == ["value", "notice", "error", "error_debug", "status"]
    assert answer["value"] is None
    assert answer["notice"] == []
    assert answer["status"] is False
    assert "Отказ" in answer["error"]
    assert "SQLSTATE" in answer["error_debug"]
    assert db.calls_of(NOTICE) == []


async def test_paid_lost_database_is_an_error(client: AsyncClient, db: Database) -> None:
    db.responses[PAID] = InterfaceError("connection closed")

    # The test client passes on what becomes 500 in the application.
    with pytest.raises(InterfaceError):
        await client.get(PAID_ROUTE)


async def test_paid_overlap_does_not_run_the_function(client: AsyncClient, db: Database) -> None:
    db.lock_is_held = True

    response = await client.get(PAID_ROUTE)

    assert response.status_code == 200
    assert json.loads(response.content)["status"] is False
    assert db.calls_of(PAID) == []
