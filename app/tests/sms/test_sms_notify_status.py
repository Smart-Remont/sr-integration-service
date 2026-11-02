"""``sms-notify-status``: reading the batches and recording what Kcell answers."""

import httpx
import pytest
from asyncpg.exceptions import RaiseError
from httpx import AsyncClient

from tests.sms.fakes import (
    READ_BATCHES,
    SET_STATUS,
    STATUS_PASSWORD,
    STATUS_ROUTE,
    STATUS_URL,
    STATUS_USER,
    Database,
    Kcell,
    assert_text,
    basic,
)


async def test_status_request_is_exactly_what_php_sends(
    client: AsyncClient,
    kcell: Kcell,
) -> None:
    kcell.answer = lambda _: httpx.Response(200, json={"messages": []})

    await client.get(STATUS_ROUTE)

    request = kcell.requests[0]
    assert request.method == "GET"
    assert str(request.url) == f"{STATUS_URL}/B1"
    assert request.content == b""
    assert dict(request.headers) == {
        "host": "kcell-status.test",
        "accept": "*/*",
        "authorization": basic(STATUS_USER, STATUS_PASSWORD),
    }
    assert request.extensions["timeout"] == {
        "connect": 10.0,
        "read": 30.0,
        "write": 30.0,
        "pool": 30.0,
    }


async def test_status_with_no_batches(client: AsyncClient, db: Database, kcell: Kcell) -> None:
    db.responses[READ_BATCHES] = []

    response = await client.get(STATUS_ROUTE)

    assert_text(response, b"No batches to check.")
    assert kcell.requests == []


async def test_status_read_failure_answers_as_no_batches(
    client: AsyncClient,
    db: Database,
    kcell: Kcell,
) -> None:
    db.responses[READ_BATCHES] = RaiseError("broken")

    response = await client.get(STATUS_ROUTE)

    assert_text(response, b"No batches to check.")
    assert kcell.requests == []


async def test_status_records_each_batch_on_its_own_without_an_open_transaction(
    client: AsyncClient,
    db: Database,
    kcell: Kcell,
) -> None:
    db.responses[READ_BATCHES] = [{"batch_id": "B1"}, {"batch_id": "B2"}]
    kcell.answer = lambda request: httpx.Response(
        200,
        json={"messages": [{"client_message_id": request.url.path[-2:], "status": "D"}]},
    )

    response = await client.get(STATUS_ROUTE)

    assert_text(response, b"0")
    assert [str(request.url) for request in kcell.requests] == [
        f"{STATUS_URL}/B1",
        f"{STATUS_URL}/B2",
    ]
    assert db.calls_of(SET_STATUS) == [
        ('[{"client_message_id":"B1","status":"D"}]',),
        ('[{"client_message_id":"B2","status":"D"}]',),
    ]
    assert kcell.transactions_during_calls == [0, 0]
    assert db.events == [
        READ_BATCHES,
        "commit",
        "kcell",
        SET_STATUS,
        "commit",
        "kcell",
        SET_STATUS,
        "commit",
    ]


@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(200, json={"messages": []}),
        httpx.Response(200, json={"other": 1}),
        httpx.Response(401, json={"error": "unauthorized"}),
        httpx.Response(502, text="<html>Bad gateway</html>"),
        httpx.Response(200, text=""),
    ],
)
async def test_status_without_messages_writes_nothing(
    client: AsyncClient,
    db: Database,
    kcell: Kcell,
    answer: httpx.Response,
) -> None:
    kcell.answer = lambda _: answer

    response = await client.get(STATUS_ROUTE)

    assert_text(response, b"0")
    assert db.calls_of(SET_STATUS) == []


async def test_status_failures_do_not_stop_the_next_batch(
    client: AsyncClient,
    db: Database,
    kcell: Kcell,
) -> None:
    db.responses[READ_BATCHES] = [{"batch_id": "B1"}, {"batch_id": "B2"}, {"batch_id": "B3"}]
    db.responses[SET_STATUS] = RaiseError("broken")
    answers = iter(
        [
            httpx.ReadTimeout("slow"),
            httpx.Response(200, json={"messages": [{"status": "D"}]}),
            httpx.Response(200, json={"messages": [{"status": "F"}]}),
        ],
    )

    def answer(_: httpx.Request) -> httpx.Response:
        result = next(answers)
        if isinstance(result, Exception):
            raise result
        return result

    kcell.answer = answer

    response = await client.get(STATUS_ROUTE)

    assert_text(response, b"0")
    assert len(kcell.requests) == 3
    assert len(db.calls_of(SET_STATUS)) == 2
