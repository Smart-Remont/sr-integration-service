"""``sms-notify`` and ``sms-notify-status`` moved from PHP ``IntegrationController``.

Kcell is ``httpx.MockTransport`` and the database is a fake connection: nothing here reaches a real
service, and the routes are never run against a real queue. Expected values come from reading PHP.
"""

import asyncio
import base64
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from itertools import chain
from typing import Any

import httpx
import pytest
from asyncpg.exceptions import RaiseError
from httpx import ASGITransport, AsyncClient
from loguru import logger

from src import repository
from src.config import kcell_config, sms_cron_auth_config
from src.database.pool import get_db_pool
from src.features.sms import limits
from src.features.sms.client import KcellClient
from src.features.sms.deps import get_kcell_client
from src.main import app
from tests.big_integration.test_contract import FakeConnection

NOTIFY_ROUTE = "/api/sms/sms-notify"
STATUS_ROUTE = "/api/sms/sms-notify-status"
SEND_URL = "https://kcell.test/hermes/batches"
STATUS_URL = "https://kcell-status.test/api/batches"
HTML_TYPE = "text/html; charset=UTF-8"
CRON_USER = "cron-user"
CRON_PASSWORD = "cron-password"
SEND_USER = "send-user"
SEND_PASSWORD = "send#pass"
STATUS_USER = "status-user"
STATUS_PASSWORD = "status-pass"
PHONE = "77001234567"
TEXT = "Секретный текст http://a/b"

LOCK = "pg_catalog.pg_try_advisory_lock"
READ_QUEUE = "notify.sms_notify__read"
SET_RESULT = "notify.sms_notify_result__set"
READ_BATCHES = "notify.sms_notify_batch__read"
SET_STATUS = "notify.sms_notify_status__set"
ACCEPTED = {"batch_id": "B1", "created_at": "2026-11-02T10:00:00Z"}


def basic(user: str, password: str) -> str:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return f"Basic {token}"


CRON_HEADERS = {"Authorization": basic(CRON_USER, CRON_PASSWORD)}


def queue_row(message_id: int) -> dict[str, Any]:
    return {
        "client_message_id": message_id,
        "sender": "SmartRemont",
        "recipient": PHONE,
        "message_text": TEXT,
    }


class TrackedConnection(FakeConnection):
    def __init__(self, database: Database, responses: dict[str, Any]) -> None:
        super().__init__(responses)
        self.database = database

    def _answer(self, function: str, args: tuple[Any, ...]) -> list[dict[str, Any]]:
        self.database.events.append(function)
        if function == LOCK:
            return self._take_lock(args)
        return super()._answer(function, args)

    def _take_lock(self, args: tuple[Any, ...]) -> list[dict[str, Any]]:
        self.calls.append((LOCK, args))
        response = self.responses[LOCK]
        if isinstance(response, Exception):
            raise response
        if self.database.lock_is_held:
            return [{"pg_try_advisory_lock": False}]
        self.database.lock_is_held = True
        return [{"pg_try_advisory_lock": True}]

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        await self._hang_if_told(query)
        return await super().fetch(query, *args)

    async def execute(self, query: str, *args: Any) -> None:
        await self._hang_if_told(query)
        await super().execute(query, *args)

    async def _hang_if_told(self, query: str) -> None:
        if any(function in query for function in self.database.hung_functions):
            self.database.events.append("hung")
            await asyncio.Event().wait()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        self.database.open_transactions += 1
        try:
            yield
            self.database.events.append("commit")
        except BaseException:
            self.database.events.append("rollback")
            if self.database.rollback_hangs:
                await asyncio.Event().wait()
            raise
        finally:
            self.database.open_transactions -= 1


class Database:
    """A fake pool: one connection, and a send-run lock that ends with its release."""

    def __init__(self) -> None:
        self.responses: dict[str, Any] = {
            LOCK: [],
            READ_QUEUE: [queue_row(101)],
            SET_RESULT: [{"id": 0}],
            READ_BATCHES: [{"batch_id": "B1"}],
            SET_STATUS: [{"id": 0}],
        }
        self.events: list[str] = []
        self.open_transactions = 0
        self.lock_is_held = False
        self.hung_functions: set[str] = set()
        self.rollback_hangs = False
        self.acquire_hangs = False
        self.release_fails = False
        self.acquired = 0
        self.released = 0
        self.route = TrackedConnection(self, self.responses)
        self.separate = TrackedConnection(self, self.responses)

    async def _acquire_route(self) -> TrackedConnection:
        if self.acquire_hangs:
            await asyncio.Event().wait()
        self.acquired += 1
        return self.route

    def acquire(self) -> Any:
        return _Acquire(self)

    async def release(self, connection: TrackedConnection, **options: float) -> None:
        assert connection is self.route
        assert options == {"timeout": 60.0}
        self.released += 1
        self.lock_is_held = False
        if self.release_fails:
            raise TimeoutError

    def calls_of(self, function: str) -> list[tuple[Any, ...]]:
        calls = [*self.route.calls, *self.separate.calls]
        return [args for name, args in calls if name == function]


class _Acquire:
    """``await pool.acquire()`` gives the route connection, ``async with`` a separate one."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def __await__(self) -> Any:
        return self.database._acquire_route().__await__()

    async def __aenter__(self) -> TrackedConnection:
        return self.database.separate

    async def __aexit__(self, *_: object) -> None:
        return None


class Kcell:
    """Answers from a per-test script, and notes the open transactions at each call."""

    def __init__(self, database: Database) -> None:
        self.database = database
        self.requests: list[httpx.Request] = []
        self.transactions_during_calls: list[int] = []
        self.answer: Callable[[httpx.Request], httpx.Response] = lambda _: httpx.Response(
            200,
            json=ACCEPTED,
        )

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        self.transactions_during_calls.append(self.database.open_transactions)
        self.database.events.append("kcell")
        return self.answer(request)

    def bodies(self) -> list[dict[str, Any]]:
        return [json.loads(request.content) for request in self.requests]


@pytest.fixture(autouse=True)
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sms_cron_auth_config, "username", CRON_USER)
    monkeypatch.setattr(sms_cron_auth_config, "password", CRON_PASSWORD)
    monkeypatch.setattr(kcell_config, "send_url", SEND_URL)
    monkeypatch.setattr(kcell_config, "send_username", SEND_USER)
    monkeypatch.setattr(kcell_config, "send_password", SEND_PASSWORD)
    monkeypatch.setattr(kcell_config, "status_url", STATUS_URL)
    monkeypatch.setattr(kcell_config, "status_username", STATUS_USER)
    monkeypatch.setattr(kcell_config, "status_password", STATUS_PASSWORD)


@pytest.fixture
def db() -> Database:
    return Database()


@pytest.fixture
def kcell(db: Database) -> Kcell:
    return Kcell(db)


@pytest.fixture
async def client(
    db: Database,
    kcell: Kcell,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[AsyncClient]:
    async def _pool() -> Database:
        return db

    monkeypatch.setattr(repository.base, "get_db_pool", _pool)
    app.dependency_overrides[get_db_pool] = _pool
    app.dependency_overrides[get_kcell_client] = lambda: KcellClient(
        kcell_config,
        transport=httpx.MockTransport(kcell.handle),
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers=CRON_HEADERS,
    ) as http:
        yield http
    app.dependency_overrides.clear()


@pytest.fixture
def log_lines() -> Any:
    lines: list[str] = []
    sink = logger.add(lines.append, format="{level} {message} {extra}", level="DEBUG")
    yield lines
    logger.remove(sink)


def assert_text(response: httpx.Response, text: bytes) -> None:
    assert response.status_code == 200
    assert response.content == text
    assert response.headers["content-type"] == HTML_TYPE


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


# --- sms-notify: answers on an empty queue and on failures ----------------------------------------


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


# --- sms-notify-status ----------------------------------------------------------------------------


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


# --- Caller check and configuration -------------------------------------------------------------


@pytest.mark.parametrize("route", [NOTIFY_ROUTE, STATUS_ROUTE])
@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": basic(CRON_USER, "wrong")}, {"Authorization": basic("wrong", "x")}],
)
async def test_wrong_caller_is_refused_before_anything_runs(
    client: AsyncClient,
    db: Database,
    kcell: Kcell,
    route: str,
    headers: dict[str, str],
) -> None:
    client.headers.pop("Authorization")

    response = await client.get(route, headers=headers)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Basic"
    assert db.events == []
    assert kcell.requests == []


@pytest.mark.parametrize("route", [NOTIFY_ROUTE, STATUS_ROUTE])
@pytest.mark.parametrize("field", ["username", "password"])
async def test_missing_cron_credentials_are_503(
    client: AsyncClient,
    db: Database,
    kcell: Kcell,
    monkeypatch: pytest.MonkeyPatch,
    route: str,
    field: str,
) -> None:
    monkeypatch.setattr(sms_cron_auth_config, field, "")

    response = await client.get(route)

    assert response.status_code == 503
    assert db.events == []
    assert kcell.requests == []


@pytest.mark.parametrize(
    ("route", "field"),
    [
        (NOTIFY_ROUTE, "send_url"),
        (NOTIFY_ROUTE, "send_username"),
        (NOTIFY_ROUTE, "send_password"),
        (STATUS_ROUTE, "status_url"),
        (STATUS_ROUTE, "status_username"),
        (STATUS_ROUTE, "status_password"),
    ],
)
async def test_missing_kcell_config_is_503_before_the_database(
    client: AsyncClient,
    db: Database,
    kcell: Kcell,
    monkeypatch: pytest.MonkeyPatch,
    route: str,
    field: str,
) -> None:
    monkeypatch.setattr(kcell_config, field, "")

    response = await client.get(route)

    assert response.status_code == 503
    assert db.events == []
    assert kcell.requests == []


@pytest.mark.parametrize(
    ("route", "other_field"),
    [(NOTIFY_ROUTE, "status_url"), (STATUS_ROUTE, "send_url")],
)
async def test_each_route_needs_only_its_own_kcell_config(
    client: AsyncClient,
    kcell: Kcell,
    monkeypatch: pytest.MonkeyPatch,
    route: str,
    other_field: str,
) -> None:
    monkeypatch.setattr(kcell_config, other_field, "")

    response = await client.get(route)

    assert response.status_code == 200
    assert len(kcell.requests) == 1


# --- Addresses and logs -------------------------------------------------------------------------


@pytest.mark.parametrize("path", [NOTIFY_ROUTE, f"{NOTIFY_ROUTE}/", f"{NOTIFY_ROUTE}/key/value"])
async def test_zend_address_forms_and_any_method_work_as_in_php(
    client: AsyncClient,
    path: str,
) -> None:
    assert_text(await client.get(path), b"0")
    assert_text(await client.post(path), b"0")


@pytest.mark.parametrize("path", [NOTIFY_ROUTE, f"{NOTIFY_ROUTE}/key/value"])
async def test_every_address_form_checks_the_caller(
    client: AsyncClient,
    kcell: Kcell,
    path: str,
) -> None:
    client.headers.pop("Authorization")

    assert (await client.post(path)).status_code == 401
    assert kcell.requests == []


async def test_logs_have_no_phone_and_no_text(
    client: AsyncClient,
    db: Database,
    kcell: Kcell,
    log_lines: list[str],
) -> None:
    failing = f"invalid input: {PHONE} {TEXT}"
    db.responses[READ_QUEUE] = [queue_row(number) for number in range(1, 402)]
    db.responses[SET_RESULT] = RaiseError(failing)
    db.responses[SET_STATUS] = RaiseError(failing)
    answers = iter(
        [
            httpx.ReadTimeout(failing),
            httpx.Response(
                500, text=f'{{"error": "{PHONE} {TEXT}", "results": [{{"to": "{PHONE}"}}]}}'
            ),
            httpx.Response(200, json={"messages": [{"to": PHONE}]}),
        ],
    )

    def answer(_: httpx.Request) -> httpx.Response:
        result = next(answers)
        if isinstance(result, Exception):
            raise result
        return result

    kcell.answer = answer

    await client.get(NOTIFY_ROUTE)
    await client.get(STATUS_ROUTE)

    logged = "".join(log_lines)
    assert "messages stay queued" in logged
    assert PHONE not in logged
    assert TEXT not in logged


async def test_read_failure_log_has_no_database_text(
    client: AsyncClient,
    db: Database,
    log_lines: list[str],
) -> None:
    db.responses[READ_QUEUE] = RaiseError(f"{{bad {PHONE}}}")
    db.responses[READ_BATCHES] = RaiseError(f"{{bad {PHONE}}}")

    await client.get(NOTIFY_ROUTE)
    await client.get(STATUS_ROUTE)

    assert "sms_notify__read" in "".join(log_lines)
    assert PHONE not in "".join(log_lines)


async def test_failed_status_on_a_recorded_batch_is_logged_with_the_batch_only(
    client: AsyncClient,
    kcell: Kcell,
    log_lines: list[str],
) -> None:
    def answer(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout(PHONE)

    kcell.answer = answer

    await client.get(STATUS_ROUTE)

    logged = "".join(log_lines)
    assert "B1" in logged
    assert PHONE not in logged
