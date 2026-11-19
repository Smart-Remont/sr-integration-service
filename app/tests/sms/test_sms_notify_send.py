"""``sms-notify``: the request to Kcell, what is recorded, and when."""

from itertools import chain

import httpx
import pytest
from httpx import AsyncClient

from src.shared import cron_run as limits
from tests.sms.fakes import (
    ACCEPTED,
    LOCK,
    NOTIFY_ROUTE,
    PHONE,
    READ_QUEUE,
    SEND_PASSWORD,
    SEND_URL,
    SEND_USER,
    SET_RESULT,
    Database,
    Kcell,
    assert_text,
    basic,
    queue_row,
)

# --- sms-notify: the request to Kcell -----------------------------------------------------------


async def test_send_request_is_exactly_what_php_sends(
    client: AsyncClient,
    db: Database,
    kcell: Kcell,
) -> None:
    db.responses[READ_QUEUE] = [
        {
            "client_message_id": 7,
            "sender": "SR/Тест",
            "recipient": PHONE,
            "message_text": 'Код "1"\nhttps://a/b',
        },
        {"client_message_id": 8, "sender": None, "recipient": PHONE, "message_text": "x"},
    ]

    await client.get(NOTIFY_ROUTE)

    request = kcell.requests[0]
    body = (
        "{\n"
        '    "messages": [\n'
        "        {\n"
        '            "client_message_id": 7,\n'
        '            "sender": "SR/Тест",\n'
        f'            "recipient": "{PHONE}",\n'
        '            "message_text": "Код \\"1\\"\\nhttps://a/b"\n'
        "        },\n"
        "        {\n"
        '            "client_message_id": 8,\n'
        '            "sender": null,\n'
        f'            "recipient": "{PHONE}",\n'
        '            "message_text": "x"\n'
        "        }\n"
        "    ],\n"
        '    "fail-fast": false\n'
        "}"
    ).encode()
    assert request.method == "POST"
    assert str(request.url) == SEND_URL
    assert request.content == body
    assert dict(request.headers) == {
        "host": "kcell.test",
        "accept": "*/*",
        "content-type": "application/json",
        "content-length": str(len(body)),
        "authorization": basic(SEND_USER, SEND_PASSWORD),
    }


async def test_send_has_explicit_timeouts(client: AsyncClient, kcell: Kcell) -> None:
    await client.get(NOTIFY_ROUTE)

    assert kcell.requests[0].extensions["timeout"] == {
        "connect": 10.0,
        "read": 300.0,
        "write": 30.0,
        "pool": 10.0,
    }


@pytest.mark.parametrize(
    ("queued", "sizes"),
    [
        (1, [1]),
        (199, [199]),
        (200, [200]),
        (201, [200, 1]),
        (400, [200, 200]),
        (401, [200, 200, 1]),
    ],
)
async def test_batches_of_200_in_queue_order(
    client: AsyncClient,
    db: Database,
    kcell: Kcell,
    queued: int,
    sizes: list[int],
) -> None:
    db.responses[READ_QUEUE] = [queue_row(number) for number in range(1, queued + 1)]

    response = await client.get(NOTIFY_ROUTE)

    assert_text(response, b"0")
    sent = [
        [message["client_message_id"] for message in body["messages"]] for body in kcell.bodies()
    ]
    assert [len(batch) for batch in sent] == sizes
    assert list(chain.from_iterable(sent)) == list(range(1, queued + 1))
    assert all(body["fail-fast"] is False for body in kcell.bodies())
    assert [args[0] for args in db.calls_of(SET_RESULT)] == sent


# --- sms-notify: what is recorded, and when -------------------------------------------------------


async def test_success_records_what_kcell_answered(
    client: AsyncClient,
    db: Database,
    kcell: Kcell,
) -> None:
    raw = '{"batch_id": "B1",  "created_at": "2026-11-02T10:00:00Z", "results": []}'
    kcell.answer = lambda _: httpx.Response(200, text=raw)

    response = await client.get(NOTIFY_ROUTE)

    assert_text(response, b"0")
    assert db.calls_of(SET_RESULT) == [([101], "B1", "2026-11-02T10:00:00Z", raw, None)]


async def test_partial_failure_records_the_per_message_results(
    client: AsyncClient,
    db: Database,
    kcell: Kcell,
) -> None:
    results = [{"client_message_id": "101", "error": "a/b", "details": {}}]
    kcell.answer = lambda _: httpx.Response(200, json={**ACCEPTED, "results": results})

    response = await client.get(NOTIFY_ROUTE)

    assert_text(response, b"0")
    _, batch_id, _, _, recorded = db.calls_of(SET_RESULT)[0]
    assert batch_id == "B1"
    # A PHP array cannot tell an empty object from an empty list, as json_decode($x, true) does.
    assert recorded == '[{"client_message_id":"101","error":"a/b","details":[]}]'


@pytest.mark.parametrize("empty", [None, [], {}, "", "0", 0, False])
async def test_empty_results_are_null(
    client: AsyncClient,
    db: Database,
    kcell: Kcell,
    empty: object,
) -> None:
    kcell.answer = lambda _: httpx.Response(200, json={**ACCEPTED, "results": empty})

    await client.get(NOTIFY_ROUTE)

    assert db.calls_of(SET_RESULT)[0][4] is None


async def test_no_transaction_is_open_during_the_kcell_call(
    client: AsyncClient,
    db: Database,
    kcell: Kcell,
) -> None:
    db.responses[READ_QUEUE] = [queue_row(number) for number in range(1, 202)]

    await client.get(NOTIFY_ROUTE)

    assert kcell.transactions_during_calls == [0, 0]
    assert db.events == [
        LOCK,
        READ_QUEUE,
        "commit",
        "kcell",
        SET_RESULT,
        "commit",
        "kcell",
        SET_RESULT,
        "commit",
    ]
    assert db.separate.calls == []
    assert (db.acquired, db.released) == (1, 1)


async def test_overlapping_run_sends_nothing(
    client: AsyncClient,
    db: Database,
    kcell: Kcell,
) -> None:
    db.lock_is_held = True

    response = await client.get(NOTIFY_ROUTE)

    assert_text(response, b"Previous run is not finished.")
    assert kcell.requests == []
    assert db.calls_of(READ_QUEUE) == []


@pytest.fixture
def short_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(limits, "DB_STEP_SECONDS", 0.01)
    monkeypatch.setattr(limits, "ROLLBACK_MARGIN_SECONDS", 0.01)


@pytest.mark.usefixtures("short_limits")
async def test_hung_record_ends_the_run_and_the_next_run_proceeds(
    client: AsyncClient,
    db: Database,
    kcell: Kcell,
    log_lines: list[str],
) -> None:
    db.responses[READ_QUEUE] = [queue_row(number) for number in range(1, 402)]
    db.hung_functions = {SET_RESULT}

    response = await client.get(NOTIFY_ROUTE)

    assert_text(response, b"0")
    assert len(kcell.requests) == 1
    assert (db.released, db.lock_is_held) == (1, False)
    assert db.open_transactions == 0
    assert "TimeoutError" in "".join(log_lines)
    assert "ERROR" in "".join(log_lines)

    db.hung_functions = set()
    response = await client.get(NOTIFY_ROUTE)

    assert_text(response, b"0")
    assert len(kcell.requests) == 4
    assert len(db.calls_of(SET_RESULT)) == 3
    assert db.released == 2


@pytest.mark.usefixtures("short_limits")
@pytest.mark.parametrize("hung", [LOCK, READ_QUEUE])
async def test_hung_lock_or_read_ends_the_run_without_sending(
    client: AsyncClient,
    db: Database,
    kcell: Kcell,
    hung: str,
) -> None:
    db.hung_functions = {hung}

    response = await client.get(NOTIFY_ROUTE)

    assert_text(response, b"No messages to send.")
    assert kcell.requests == []
    assert (db.released, db.lock_is_held) == (1, False)


@pytest.mark.usefixtures("short_limits")
async def test_rollback_that_hangs_after_the_limit_still_ends_the_run(
    client: AsyncClient,
    db: Database,
    kcell: Kcell,
) -> None:
    db.hung_functions = {SET_RESULT}
    db.rollback_hangs = True

    response = await client.get(NOTIFY_ROUTE)

    assert_text(response, b"0")
    assert len(kcell.requests) == 1
    assert (db.released, db.lock_is_held) == (1, False)


@pytest.mark.usefixtures("short_limits")
async def test_hung_acquire_is_503_without_sending(
    client: AsyncClient,
    db: Database,
    kcell: Kcell,
    log_lines: list[str],
) -> None:
    db.acquire_hangs = True

    response = await client.get(NOTIFY_ROUTE)

    assert response.status_code == 503
    assert kcell.requests == []
    assert db.released == 0
    assert "acquire" in "".join(log_lines)


async def test_failed_release_is_logged_and_keeps_the_answer(
    client: AsyncClient,
    db: Database,
    log_lines: list[str],
) -> None:
    db.release_fails = True

    assert_text(await client.get(NOTIFY_ROUTE), b"0")
    assert "release" in "".join(log_lines)


# --- what the contour comparison found -----------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "bound"),
    [
        ("true", "1"),
        ("false", ""),
        ("[1]", "Array"),
        ('{"a":1}', "Array"),
        ("1.5", "1.5"),
        ("123", "123"),
        ("null", None),
    ],
)
async def test_answer_values_are_bound_as_php_binds_them(
    client: AsyncClient,
    db: Database,
    kcell: Kcell,
    value: str,
    bound: str | None,
) -> None:
    db.responses[READ_QUEUE] = [queue_row(1)]
    kcell.answer = lambda _: httpx.Response(
        200,
        content=f'{{"batch_id":{value},"created_at":{value}}}'.encode(),
    )

    await client.get(NOTIFY_ROUTE)

    args = db.calls_of(SET_RESULT)[0]
    assert args[1:3] == (bound, bound)


async def test_answer_that_is_a_json_string_records_its_first_byte_as_php_does(
    client: AsyncClient,
    db: Database,
    kcell: Kcell,
) -> None:
    db.responses[READ_QUEUE] = [queue_row(1)]
    kcell.answer = lambda _: httpx.Response(200, content=b'"abc"')

    await client.get(NOTIFY_ROUTE)

    assert db.calls_of(SET_RESULT)[0][1:3] == ("a", "a")
    assert db.calls_of(SET_RESULT)[0][4] is None


async def test_answer_string_with_a_multibyte_letter_records_nothing_and_run_goes_on(
    client: AsyncClient,
    db: Database,
    kcell: Kcell,
) -> None:
    """The server refuses one byte of a letter, and that is no reason to stop the other batches."""
    db.responses[READ_QUEUE] = [queue_row(number) for number in range(1, 202)]
    answers = iter([b'"\xd0\xb0\xd0\xb1"', b'{"batch_id":"B2","created_at":"x"}'])
    kcell.answer = lambda _: httpx.Response(200, content=next(answers))

    response = await client.get(NOTIFY_ROUTE)

    assert_text(response, b"0")
    assert len(kcell.requests) == 2
    assert [args[0] for args in db.calls_of(SET_RESULT)] == [[201]]


async def test_cron_answer_has_the_session_headers_of_php(
    client: AsyncClient,
    db: Database,
) -> None:
    db.responses[READ_QUEUE] = []

    response = await client.get(NOTIFY_ROUTE)

    assert response.headers["cache-control"] == "no-store, no-cache, must-revalidate"
    assert response.headers["pragma"] == "no-cache"
