"""The fake database and MyNCA of the auto-sign tests.

MyNCA is ``httpx.MockTransport``, the database is a fake connection: nothing here
reaches a real service, no key is real, and the routes never run against a real database. Expected
values come from reading PHP.
"""

import asyncio
import base64
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

import httpx

from tests.big_integration.fakes import FakeConnection

OPERATOR_ROUTE = "/api/auto_sign/cron-auto-sign-operator"
UPLOAD_ROUTE = "/api/auto_sign/cron-auto-upload-sign-doc"
HTML_TYPE = "text/html; charset=UTF-8"

CRON_USER = "auto-sign-cron"
CRON_PASSWORD = "auto-sign-cron-password"
MYNCA_URL = "https://mynca.test/api"
MYNCA_TOKEN = "mynca-token-secret"
MASTER_KEY = "master-key-secret"
KEY_BYTES = b"\x00\x01PKCS12-key-bytes-secret\xff\xfe"
KEY_BASE64 = base64.b64encode(KEY_BYTES).decode()
KEY_PASSWORD = "key-password-secret"
IIN = "900101300123"
DN_NAME = f"CN=OPERATOR,SERIALNUMBER=IIN{IIN}"
FILE_BASE64 = "RklMRS1CT0RZLVNFQ1JFVA=="
PDF_BYTES = b"%PDF-1.7 SIGNED-BODY-SECRET"

CLIENT_PROCESS = "11111111-1111-4111-8111-111111111111"
NEW_PROCESS = "22222222-2222-4222-8222-222222222222"
GROUP = "33333333-3333-4333-8333-333333333333"
NEW_GROUP = "44444444-4444-4444-8444-444444444444"
SIGN_ID = 700
NEW_SIGN_ID = 900

LOCK = "pg_catalog.pg_try_advisory_lock"
READ_ROWS = "public.sign_read_for_auto_sign_operator"
READ_KEY = "nca.company_key_store__get_active_by_company"
INSERT_SIGN = "client.insert_sign_general"
MODIFY_SIGN = "client.sign_tab__modify"
FIND_SIGN = "client.sign_tab__id_by_process"
READ_DOCS = "public.sign_read_for_doc"
READ_GROUP = "public.sign_group_id_get_by_client_request"
UPDATE_DOC = "sale.sign_document_upd"
TERMINATE = "pg_catalog.pg_terminate_backend"
SESSION_ALIVE = "pg_catalog.pg_stat_get_activity"
OLD_SESSION = 4242
WRITES = {INSERT_SIGN, MODIFY_SIGN, UPDATE_DOC}

KEY_INFO_PATH = "/pkcs12/info"
SIGN_SAVE_PATH = "/cms/sign-save"
CLIENT_FILE_PATH = f"/sign/{CLIENT_PROCESS}/file"
ROLLBACK_PATH = f"/sign/{NEW_PROCESS}/rollback"
GROUP_PDF_PATH = f"/sign/group/{GROUP}/download"


def basic(user: str, password: str) -> str:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return f"Basic {token}"


CRON_HEADERS = {"Authorization": basic(CRON_USER, CRON_PASSWORD)}


def sign_row(**changes: Any) -> dict[str, Any]:
    """A DS signed by the client, the way asyncpg gives the cursor."""
    return {
        "sign_id": SIGN_ID,
        "agreement_client_request_id": None,
        "ds_id": 501,
        "client_request_document_id": None,
        "contractor_agreement_list_id": None,
        "sign_what": "MYNCA",
        "group_code": "DS_CLIENT_AGREEMENT",
        "doc_type": "DS",
        "sign_type_code": "CONTRACT_SIGNER_DS_SIGN",
        "sign_pid": None,
        "sign_process_id": UUID(CLIENT_PROCESS),
        "sign_group_id": UUID(GROUP),
        "sign_method": "CMS",
        "company_id": 3,
        **changes,
    }


def fail_after_dry_run(error: Exception) -> Callable[[tuple[Any, ...]], list[dict[str, int]]]:
    """``client.insert_sign_general`` that accepts the dry run and refuses the real insert."""
    calls = []

    def answer(args: tuple[Any, ...]) -> list[dict[str, int]]:
        calls.append(args)
        if len(calls) % 2 == 0:
            raise error
        return [{"insert_sign_general": NEW_SIGN_ID}]

    return answer


class Connection(FakeConnection):
    def __init__(self, database: Database, responses: dict[str, Any]) -> None:
        super().__init__(responses)
        self.database = database
        self.queries: list[str] = []

    def _answer(self, function: str, args: tuple[Any, ...]) -> list[dict[str, Any]]:
        self.database.events.append(function)
        if function == LOCK:
            return self._take_lock(args)
        answer = super()._answer(function, args)
        if function in WRITES:
            self.database.pending.append((function, args))
        return answer

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
        self.queries.append(query)
        await self._hang_if_told(query)
        return await super().fetch(query, *args)

    async def execute(self, query: str, *args: Any) -> None:
        self.queries.append(query)
        await self._hang_if_told(query)
        await super().execute(query, *args)

    def get_server_pid(self) -> int:
        return OLD_SESSION

    async def _hang_if_told(self, query: str) -> None:
        database = self.database
        for function, passing in database.passing_calls.items():
            if function in query and passing > 0:
                database.passing_calls[function] = passing - 1
                return
        if database.pause_function and database.pause_function in query:
            database.pause_function = None
            database.paused.set()
            await database.resume.wait()
        for function, remaining in self.database.hung_functions.items():
            if function in query and remaining != 0:
                self.database.hung_functions[function] = remaining - 1
                self.database.events.append("hung")
                await asyncio.Event().wait()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        database = self.database
        database.events.append("begin")
        database.open_transactions += 1
        mark = len(database.pending)
        try:
            yield
            await database.finish_commit()
            database.committed.extend(database.pending[mark:])
            del database.pending[mark:]
            database.events.append("commit")
        except BaseException:
            del database.pending[mark:]
            database.events.append("rollback")
            raise
        finally:
            database.open_transactions -= 1


class Database:
    """A fake pool: the route connection holds the run lock, which ends with its release."""

    def __init__(self) -> None:
        self.responses: dict[str, Any] = {
            LOCK: [],
            READ_ROWS: [sign_row()],
            READ_KEY: [{"key_data": KEY_BYTES, "key_password": KEY_PASSWORD}],
            INSERT_SIGN: [{"insert_sign_general": NEW_SIGN_ID}],
            MODIFY_SIGN: [{"sign_tab__modify": NEW_SIGN_ID}],
            FIND_SIGN: [],
            TERMINATE: [{"pg_terminate_backend": True}],
            SESSION_ALIVE: [],
            READ_DOCS: [{"agreement_client_request_id": 88, "sign_cnt": 1}],
            READ_GROUP: [{"sign_group_id": UUID(GROUP)}],
            UPDATE_DOC: [{"sign_document_upd": 0}],
        }
        self.events: list[str] = []
        self.pending: list[tuple[str, tuple[Any, ...]]] = []
        self.committed: list[tuple[str, tuple[Any, ...]]] = []
        self.open_transactions = 0
        self.lock_is_held = False
        # A function and how many of its calls hang; -1 is every call.
        self.hung_functions: dict[str, int] = {}
        # A function and how many of its calls go through before those hang.
        self.passing_calls: dict[str, int] = {}
        self.commit_error: Exception | None = None
        self.commit_hangs = False
        self.acquire_hangs = False
        # One call of this function waits for ``resume``, so a test can start another run meanwhile.
        self.pause_function: str | None = None
        self.paused = asyncio.Event()
        self.resume = asyncio.Event()
        self.released = 0
        self.route = Connection(self, self.responses)
        self.separate = Connection(self, self.responses)

    async def finish_commit(self) -> None:
        if not self.pending:
            return
        if self.commit_hangs:
            self.events.append("hung")
            await asyncio.Event().wait()
        if self.commit_error is not None:
            raise self.commit_error

    def acquire(self) -> Any:
        return _Acquire(self)

    async def release(self, connection: Connection, **options: float) -> None:
        assert connection is self.route
        assert options == {"timeout": 60.0}
        self.released += 1
        self.lock_is_held = False

    def calls_of(self, function: str) -> list[tuple[Any, ...]]:
        calls = [*self.route.calls, *self.separate.calls]
        return [args for name, args in calls if name == function]

    def committed_functions(self) -> list[str]:
        return [function for function, _ in self.committed]


class _Acquire:
    """``await pool.acquire()`` gives the route connection, ``async with`` a separate one."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def __await__(self) -> Any:
        return self._route().__await__()

    async def _route(self) -> Connection:
        if self.database.acquire_hangs:
            await asyncio.Event().wait()
        return self.database.route

    async def __aenter__(self) -> Connection:
        return self.database.separate

    async def __aexit__(self, *_: object) -> None:
        return None


type Answer = httpx.Response | Exception | Callable[[httpx.Request], httpx.Response]


class Mynca:
    """Answers per path from a per-test script, and notes the open transactions at each call."""

    def __init__(self, database: Database) -> None:
        self.database = database
        self.requests: list[httpx.Request] = []
        self.transactions_during_calls: list[int] = []
        self.answers: dict[str, Answer] = {
            KEY_INFO_PATH: httpx.Response(200, json={"valid": True}),
            CLIENT_FILE_PATH: httpx.Response(200, json={"data": {"file_content": FILE_BASE64}}),
            SIGN_SAVE_PATH: httpx.Response(
                200,
                json={"sign_process_id": NEW_PROCESS, "dn_name": DN_NAME, "group_id": NEW_GROUP},
            ),
            ROLLBACK_PATH: httpx.Response(200, json={"ok": True}),
            GROUP_PDF_PATH: httpx.Response(
                200,
                content=PDF_BYTES,
                headers={"Content-Type": "application/pdf"},
            ),
        }

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/api")
        self.requests.append(request)
        self.transactions_during_calls.append(self.database.open_transactions)
        self.database.events.append(f"mynca {request.method} {path}")
        answer = self.answers[path]
        if isinstance(answer, Exception):
            raise answer
        return answer(request) if callable(answer) else answer

    def paths(self) -> list[str]:
        return [request.url.path.removeprefix("/api") for request in self.requests]

    def request_to(self, path: str) -> httpx.Request:
        return next(req for req in self.requests if req.url.path.removeprefix("/api") == path)

    def sign_save_bodies(self) -> list[dict[str, Any]]:
        return [
            json.loads(request.content)
            for request in self.requests
            if request.url.path.endswith(SIGN_SAVE_PATH)
        ]


def assert_empty(response: httpx.Response) -> None:
    assert response.status_code == 200
    assert response.content == b""
    assert response.headers["content-type"] == HTML_TYPE
