"""The fake database and mail server of the mailing crons. Nothing here reaches a real server."""

import asyncio
import base64
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from src.features.mailing.message import ComposedLetter
from src.features.mailing.smtp import SmtpError, SmtpSettings
from tests.big_integration.fakes import FakeConnection

CRON_USER = "cron-user"
CRON_PASSWORD = "cron-password"
FROM_ADDRESS = "notify@test.invalid"
CC_ADDRESS = "copy@test.invalid"
LINK_BASE = "https://office.test"

LOCK = "pg_catalog.pg_try_advisory_lock"
SET_CONFIG = "pg_catalog.set_config"
READ_QUEUE = "public.read_send_email"
READ_ATTACHMENTS = "public.read_send_email_attachment"
CHECK = "public.email_check_employee_subscribe"
INFO = "public.email_get_unsubscribe_info"
TOKEN = "public.email_get_remont_token"
HARD = "public.email_unsubscribe_hard"
MARK = "public.upd_email_send"
FAILED = "public.upd_email_send_failed"
FILL = "public.fill_send_email"
PAID = "public.change_paid_client_request_to_remont"
NOTICE = "public.get_session_notice"


def basic(user: str, password: str) -> str:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return f"Basic {token}"


CRON_HEADERS = {"Authorization": basic(CRON_USER, CRON_PASSWORD)}


def queue_row(send_email_id: int, email: str = "one@test.invalid", **fields: Any) -> dict[str, Any]:
    return {
        "send_email_id": send_email_id,
        "email": email,
        "what": "TEMPLATE_MESSAGE",
        "email_content": "<p>Текст</p>",
        "email_title": "Тема",
        **fields,
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
        return [{"pg_try_advisory_lock": not self.database.lock_is_held}]

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        self.database.open_transactions += 1
        try:
            yield
            self.database.events.append("commit")
        except BaseException:
            self.database.events.append("rollback")
            raise
        finally:
            self.database.open_transactions -= 1


class Database:
    """A fake pool with one connection."""

    def __init__(self) -> None:
        self.responses: dict[str, Any] = {
            SET_CONFIG: [{"set_config": ""}],
            READ_QUEUE: [],
            READ_ATTACHMENTS: [],
            CHECK: [{"id": 1}],
            INFO: [
                {
                    "token": "TOK",
                    "employee_fio": "Фио",
                    "employee_id": 7,
                    "employee_type": "EMPLOYEE",
                }
            ],
            TOKEN: [{"id": "REMONT-TOK"}],
            HARD: [{"id": 0}],
            MARK: [{"id": 1}],
            FAILED: [{"id": 1}],
            FILL: [{"id": 0}],
            PAID: [{"id": 0}],
            NOTICE: [{"id": ""}],
        }
        self.events: list[str] = []
        self.open_transactions = 0
        self.lock_is_held = False
        self.route = TrackedConnection(self, self.responses)

    async def _acquire(self) -> TrackedConnection:
        return self.route

    def acquire(self) -> Any:
        return self._acquire()

    async def release(self, connection: TrackedConnection, **_: float) -> None:
        assert connection is self.route

    def calls_of(self, function: str) -> list[tuple[Any, ...]]:
        return [args for name, args in self.route.calls if name == function]


class MailServer:
    """What the transports of one test did. ``refuse`` maps a letter number to its failure."""

    def __init__(self, database: Database) -> None:
        self.database = database
        self.letters: list[ComposedLetter] = []
        self.settings: list[SmtpSettings] = []
        self.refuse: dict[int, SmtpError] = {}
        self.attempts = 0
        self.closed = 0
        self.transactions_during_sending: list[int] = []
        self.hangs = False

    def transport_class(self) -> type[Any]:
        server = self

        class FakeTransport:
            def __init__(self, settings: SmtpSettings) -> None:
                server.settings.append(settings)

            async def send(self, letter: ComposedLetter) -> None:
                server.attempts += 1
                server.database.events.append("smtp")
                server.transactions_during_sending.append(server.database.open_transactions)
                if server.hangs:
                    await asyncio.Event().wait()
                failure = server.refuse.get(server.attempts)
                if failure is not None:
                    raise failure
                server.letters.append(letter)

            async def close(self) -> None:
                server.closed += 1

        return FakeTransport


class StubWriter:
    """Stands for ``asyncio.StreamWriter``: keeps what was written."""

    def __init__(self) -> None:
        self.written = bytearray()
        self.closed = False

    def write(self, data: bytes) -> None:
        self.written += data

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None
