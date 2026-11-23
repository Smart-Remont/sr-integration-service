"""Fixtures of the MCP gateway tests: MySpace is an httpx mock, /mcp is the real app route."""

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from src.config import mcp_gateway_config
from src.features.mcp_gateway.deps import mcp_gateway
from src.features.mcp_gateway.tokens import RefusalCache, TokenCache
from src.main import app

MYSPACE_URL = "https://myspace.test"
GATEWAY_CLIENT_ID = "mcp-gateway"
GATEWAY_SECRET = "gateway-secret"


def srx(name: str) -> str:
    """A token of the shape MySpace issues: srx_ and 43 base64url characters."""
    return "srx_" + (name + "-" * 43)[:43]


CALLER_TOKEN = srx("caller")
MYSPACE_TOKEN = "srx_exchanged_myspace_ext"
PROTOCOL_VERSION = "2025-06-18"

type Handler = Callable[[httpx.Request], httpx.Response | Awaitable[httpx.Response]]


@dataclass
class FakeMyspace:
    """``/ext/v1`` of MySpace: one exchange answer at a time and an answer per GET path."""

    exchange_answers: list[httpx.Response] = field(default_factory=list)
    reads: dict[str, Handler | httpx.Response] = field(default_factory=dict)
    requests: list[httpx.Request] = field(default_factory=list)
    issued: int = 0
    # Seconds MySpace takes to answer the exchange: lets concurrent requests meet.
    exchange_delay: float = 0.0

    def issue(self, scope: str = "kanban.read", expires_in: int = 600) -> httpx.Response:
        self.issued += 1
        token = MYSPACE_TOKEN if self.issued == 1 else f"{MYSPACE_TOKEN}_{self.issued}"
        return httpx.Response(
            200,
            json={
                "access_token": token,
                "issued_token_type": "urn:ietf:params:oauth:token-type:access_token",
                "token_type": "Bearer",
                "expires_in": expires_in,
                "scope": scope,
            },
        )

    @property
    def exchanges(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path == "/ext/v1/auth/token/"]

    @property
    def data_reads(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path != "/ext/v1/auth/token/"]

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/ext/v1/auth/token/":
            await asyncio.sleep(self.exchange_delay)
            return self.exchange_answers.pop(0) if self.exchange_answers else self.issue()
        answer = self.reads.get(request.url.path)
        if answer is None:
            return httpx.Response(404, json={"error": "not_found", "message": "Нет такой ручки"})
        if not callable(answer):
            return answer
        result = answer(request)
        return await result if isinstance(result, Awaitable) else result


def exchange_form(request: httpx.Request) -> dict[str, str]:
    return {key: values[0] for key, values in parse_qs(request.content.decode()).items()}


@pytest.fixture
def myspace() -> FakeMyspace:
    return FakeMyspace()


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mcp_gateway_config, "myspace_ext_api_url", MYSPACE_URL)
    monkeypatch.setattr(mcp_gateway_config, "client_id", GATEWAY_CLIENT_ID)
    monkeypatch.setattr(mcp_gateway_config, "client_secret", GATEWAY_SECRET)


@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """The module gateway lives for the whole test run; each test starts with an empty cache."""
    monkeypatch.setattr(mcp_gateway, "_cache", TokenCache())
    monkeypatch.setattr(mcp_gateway, "_refusals", RefusalCache())


@asynccontextmanager
async def serve(handler: Handler) -> AsyncIterator[AsyncClient]:
    """The app with the gateway started, as the lifespan starts it, and MySpace replaced."""
    async with (
        mcp_gateway.running(transport=httpx.MockTransport(handler)),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http,
    ):
        yield http


class Mcp:
    """JSON-RPC over the Streamable HTTP POST, as an MCP client sends it."""

    def __init__(self, http: AsyncClient) -> None:
        self.http = http
        self._next_id = 0

    async def post(
        self, method: str, params: dict[str, Any] | None = None, token: str | None = CALLER_TOKEN
    ) -> httpx.Response:
        self._next_id += 1
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        body = {"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params or {}}
        return await self.http.post("/mcp", content=json.dumps(body), headers=headers)

    async def result(
        self, method: str, params: dict[str, Any] | None = None, token: str = CALLER_TOKEN
    ) -> dict[str, Any]:
        response = await self.post(method, params, token)
        assert response.status_code == 200, response.text
        body = response.json()
        assert "error" not in body, body
        return body["result"]

    async def call(
        self, tool: str, arguments: dict[str, Any] | None = None, token: str = CALLER_TOKEN
    ) -> dict[str, Any]:
        return await self.result("tools/call", {"name": tool, "arguments": arguments or {}}, token)


@asynccontextmanager
async def open_mcp(myspace: FakeMyspace) -> AsyncIterator[Mcp]:
    """Inside the test, not a fixture: the SDK's task group must start and end in one task."""
    async with serve(myspace.handle) as http:
        yield Mcp(http)
