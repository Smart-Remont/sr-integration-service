"""The fake database and Kcell of the ``sms-notify`` and ``sms-notify-status`` tests.

Kcell is ``httpx.MockTransport`` and the database is a fake connection: nothing here reaches a real
service, and the routes are never run against a real queue. Expected values come from reading PHP.
"""

import asyncio
import base64
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import httpx

from tests.big_integration.fakes import FakeConnection

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


def assert_text(response: httpx.Response, text: bytes) -> None:
    assert response.status_code == 200
    assert response.content == text
    assert response.headers["content-type"] == HTML_TYPE
