"""``sms-notify``: answers on an empty queue and on failures."""

import httpx
import pytest
from asyncpg.exceptions import RaiseError
from httpx import AsyncClient

from tests.sms.fakes import (
    ACCEPTED,
    LOCK,
    NOTIFY_ROUTE,
    READ_QUEUE,
    SET_RESULT,
    Database,
    Kcell,
    assert_text,
    queue_row,
)


async def test_empty_queue(client: AsyncClient, db: Database, kcell: Kcell) -> None:
    db.responses[READ_QUEUE] = []

    response = await client.get(NOTIFY_ROUTE)

    assert_text(response, b"No messages to send.")
    assert kcell.requests == []


async def test_queue_read_failure_answers_as_an_empty_queue(
    client: AsyncClient,
    db: Database,
    kcell: Kcell,
) -> None:
    db.responses[READ_QUEUE] = RaiseError("broken")

    response = await client.get(NOTIFY_ROUTE)

    assert_text(response, b"No messages to send.")
    assert kcell.requests == []


async def test_lock_failure_sends_nothing(
    client: AsyncClient,
    db: Database,
    kcell: Kcell,
) -> None:
    db.responses[LOCK] = RaiseError("broken")

    response = await client.get(NOTIFY_ROUTE)

    assert_text(response, b"No messages to send.")
    assert kcell.requests == []


@pytest.mark.parametrize(
    "transport_error",
    [
        httpx.ReadTimeout("slow"),
        httpx.ConnectTimeout("slow"),
        httpx.ConnectError("refused"),
        httpx.RemoteProtocolError("cut"),
    ],
)
async def test_no_answer_leaves_the_batch_queued_and_goes_on(
    client: AsyncClient,
    db: Database,
    kcell: Kcell,
    transport_error: httpx.TransportError,
) -> None:
    db.responses[READ_QUEUE] = [queue_row(number) for number in range(1, 202)]
    answers = iter([transport_error, httpx.Response(200, json=ACCEPTED)])

    def answer(_: httpx.Request) -> httpx.Response:
        result = next(answers)
        if isinstance(result, Exception):
            raise result
        return result

    kcell.answer = answer

    response = await client.get(NOTIFY_ROUTE)

    assert_text(response, b"0")
    assert len(kcell.requests) == 2
    assert [args[0] for args in db.calls_of(SET_RESULT)] == [[201]]


async def test_kcell_error_with_json_is_recorded_without_a_batch(
    client: AsyncClient,
    db: Database,
    kcell: Kcell,
) -> None:
    raw = '{"error": "unauthorized"}'
    kcell.answer = lambda _: httpx.Response(401, text=raw)

    response = await client.get(NOTIFY_ROUTE)

    assert_text(response, b"0")
    assert db.calls_of(SET_RESULT) == [([101], None, None, raw, None)]


@pytest.mark.parametrize("raw", [b"", b"<html>Bad gateway</html>", b"\xff\xfe"])
async def test_answer_that_is_not_json_records_nothing_and_run_goes_on(
    client: AsyncClient,
    db: Database,
    kcell: Kcell,
    raw: bytes,
) -> None:
    kcell.answer = lambda _: httpx.Response(502, content=raw)

    response = await client.get(NOTIFY_ROUTE)

    assert_text(response, b"0")
    assert db.calls_of(SET_RESULT) == []


@pytest.mark.parametrize("raw", ["[1, 2]", "5", "null"])
async def test_answer_that_is_not_an_object_has_no_fields(
    client: AsyncClient,
    db: Database,
    kcell: Kcell,
    raw: str,
) -> None:
    kcell.answer = lambda _: httpx.Response(200, text=raw)

    await client.get(NOTIFY_ROUTE)

    assert db.calls_of(SET_RESULT) == [([101], None, None, raw, None)]


@pytest.mark.parametrize(
    ("batch_id", "bound"),
    [("B7", "B7"), (55, "55"), ("", "")],
)
async def test_batch_id_is_bound_as_text(
    client: AsyncClient,
    db: Database,
    kcell: Kcell,
    batch_id: object,
    bound: str,
) -> None:
    kcell.answer = lambda _: httpx.Response(200, json={"batch_id": batch_id})

    await client.get(NOTIFY_ROUTE)

    assert db.calls_of(SET_RESULT)[0][1] == bound


async def test_failed_record_stops_the_run(
    client: AsyncClient,
    db: Database,
    kcell: Kcell,
) -> None:
    db.responses[READ_QUEUE] = [queue_row(number) for number in range(1, 402)]
    db.responses[SET_RESULT] = RaiseError("broken")

    response = await client.get(NOTIFY_ROUTE)

    assert_text(response, b"0")
    assert len(kcell.requests) == 1
    assert len(db.calls_of(SET_RESULT)) == 1
    assert db.events[-1] == "rollback"
    assert db.separate.calls == []
