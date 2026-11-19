"""Fake pool, database and partners. Nothing here reaches a real service."""

import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import httpx
from asyncpg.exceptions import InFailedSQLTransactionError

from src.config import ClientSignConfig
from src.features.client_sign.client import DocumentFetcher
from src.features.client_sign.deps import (
    get_client_sign_config,
    get_client_sign_database,
    get_document_fetcher,
    get_mynca_client,
)
from src.features.client_sign.repo import ClientSignDatabase
from src.main import app
from src.shared.mynca_client import MyncaClient

CONFIG = ClientSignConfig(
    mynca_base_url="https://mynca.test/api",
    mynca_token="test-mynca-token",
    public_url="https://office.test",
    office_url="https://office.test",
    myspace_api_url="https://myspace.test",
    partner_url="https://partner.test/partner",
    callback_secret="test-callback-secret",
)
LOCK = "pg_catalog.pg_advisory_xact_lock"
INSERT_SIGN = "client.insert_sign_general"
MODIFY_SIGN = "client.sign_tab__modify"
FIND_SIGN = "client.sign_tab__id_by_process"
WRITES = {INSERT_SIGN, MODIFY_SIGN}
_FUNCTION = re.compile(r"select ([\w.]+)\(")

type Handler = Callable[..., Any] | list[dict[str, Any]] | int | Exception | None


class FakeConnection:
    """Keeps writes per transaction, so tests see what a commit left and a rollback dropped."""

    def __init__(self, handlers: dict[str, Handler] | None = None) -> None:
        self.handlers = handlers or {}
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.committed: list[tuple[str, tuple[Any, ...]]] = []
        self.pending: list[tuple[str, tuple[Any, ...]]] = []
        self.fail_commit = False
        self.cursors: dict[str, list[dict[str, Any]]] = {}
        self.depth = 0

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[None]:
        mark = len(self.pending)
        self.depth += 1
        try:
            yield
        except Exception:
            del self.pending[mark:]
            raise
        finally:
            self.depth -= 1
        if self.depth == 0:
            if self.fail_commit:
                self.pending.clear()
                raise InFailedSQLTransactionError("commit failed")
            self.committed.extend(self.pending)
            self.pending.clear()

    def transaction(self) -> Any:
        return self._transaction()

    async def fetchval(self, query: str, *args: Any) -> Any:
        if "set_config" in query:
            return args[-1]
        return None

    async def execute(self, query: str, *args: Any) -> str:
        name = str(args[0])
        result = self._invoke(query, args[1:])
        self.cursors[name] = result if isinstance(result, list) else []
        return "SELECT 1"

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        cursor = re.fullmatch(r'fetch all in "(.+)"', query)
        if cursor:
            return self.cursors.pop(cursor.group(1), [])
        result = self._invoke(query, args)
        if isinstance(result, list):
            return result
        return [{"value": result}]

    def _invoke(self, query: str, args: tuple[Any, ...]) -> Any:
        match = _FUNCTION.search(query)
        assert match, query
        function = match.group(1)
        self.calls.append((function, args))
        handler = self.handlers.get(function)
        if isinstance(handler, Exception):
            raise handler
        result = handler(*args) if callable(handler) else handler
        if function in WRITES:
            self.pending.append((function, args))
        return result

    def called(self, function: str) -> list[tuple[Any, ...]]:
        return [args for name, args in self.calls if name == function]


class FakePool:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection
        self.acquired = 0

    @asynccontextmanager
    async def _acquire(self) -> AsyncIterator[FakeConnection]:
        self.acquired += 1
        yield self.connection

    def acquire(self) -> Any:
        return self._acquire()


class Partners:
    """MyNCA, document addresses and the partner backend, answering what a test sets."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.routes: dict[str, httpx.Response | Exception] = {}

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._answer)

    def _answer(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.routes.get(str(request.url))
        if isinstance(answer, Exception):
            raise answer
        if answer is None:
            return httpx.Response(404, content=b"not routed")
        return answer

    def urls(self) -> list[str]:
        return [str(request.url) for request in self.requests]


def install_overrides(
    connection: FakeConnection,
    partners: Partners,
    config: ClientSignConfig = CONFIG,
) -> None:
    app.dependency_overrides[get_client_sign_database] = lambda: ClientSignDatabase(
        FakePool(connection)
    )
    app.dependency_overrides[get_client_sign_config] = lambda: config
    app.dependency_overrides[get_mynca_client] = lambda: MyncaClient(
        config.mynca_base_url, config.mynca_token, transport=partners.transport()
    )
    app.dependency_overrides[get_document_fetcher] = lambda: DocumentFetcher(
        transport=partners.transport()
    )


def clear_overrides() -> None:
    for dependency in (
        get_client_sign_database,
        get_client_sign_config,
        get_mynca_client,
        get_document_fetcher,
    ):
        app.dependency_overrides.pop(dependency, None)
