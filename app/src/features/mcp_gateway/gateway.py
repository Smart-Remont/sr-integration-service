"""``/mcp``: the MCP server over Streamable HTTP behind the Bearer gate.

Request: Bearer sr-mcp token -> cached or freshly exchanged myspace-ext token -> the SDK, which
runs the tool; the tool reads MySpace with that token. The gateway decides nothing about access:
a token MySpace will not exchange is a 401 here (403 when a scope is missing), MySpace down or a
refusal of the gateway's own client is a 503. A token not shaped like MySpace's never leaves here.

Stateless and JSON-only: gunicorn runs several workers, and any of them must answer any request.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from loguru import logger
from mcp.server.mcpserver import MCPServer
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from src.config import McpGatewayConfig

from .client import (
    TIMEOUT,
    ExchangeRefused,
    ExchangeScopeRefused,
    MyspaceExtClient,
    MyspaceUnavailable,
    log_unavailable,
    looks_like_access_token,
)
from .kanban_tools import register_kanban_tools
from .session import CALLER_STATE_KEY, CallerSession, request_deadline
from .tokens import RefusalCache, TokenBroker, TokenCache, log_id, token_key
from .tooling import ScopedTools

MCP_PATH = "/mcp"
SERVER_NAME = "smartremont-myspace"
INSTRUCTIONS = (
    "Инструменты читают MySpace Smart Remont от имени сотрудника, с которым идёт разговор, "
    "и показывают ровно то, что он видит на экране MySpace. Только чтение: изменить ничего "
    "нельзя. Ссылки (url) в ответах ведут в веб-MySpace, их можно давать пользователю."
)

# The route takes only a Bearer token, which a page doing DNS rebinding has no way to send, and
# behind nginx the Host is the public name, which the SDK's localhost-only default would refuse.
_NO_HOST_CHECK = TransportSecuritySettings(enable_dns_rebinding_protection=False)

_INVALID_TOKEN_HEADERS = {
    "WWW-Authenticate": 'Bearer error="invalid_token"',
    "Cache-Control": "no-store",
}
_INVALID_TOKEN_BODY = {
    "error": "invalid_token",
    "message": "Токен доступа недействителен или истёк",
}
_INSUFFICIENT_SCOPE_HEADERS = {
    "WWW-Authenticate": 'Bearer error="insufficient_scope"',
    "Cache-Control": "no-store",
}
_INSUFFICIENT_SCOPE_BODY = {
    "error": "insufficient_scope",
    "message": "У токена нет областей, нужных шлюзу",
}
_UNAVAILABLE_HEADERS = {"Retry-After": "30", "Cache-Control": "no-store"}
_UNAVAILABLE_BODY = {"error": "temporarily_unavailable", "message": "MySpace временно недоступен"}
_NOT_CONFIGURED_BODY = {"error": "temporarily_unavailable", "message": "Шлюз MCP не настроен"}


def build_server() -> tuple[MCPServer, ScopedTools]:
    tools = ScopedTools()
    server = MCPServer(name=SERVER_NAME, instructions=INSTRUCTIONS, subscriptions=False)
    register_kanban_tools(server, tools)
    server.middleware.append(tools.enforce_scopes)
    return server, tools


def _bearer_token(scope: Scope) -> str | None:
    for name, value in scope["headers"]:
        if name == b"authorization":
            kind, _, token = value.decode("latin-1").partition(" ")
            token = token.strip()
            return token if kind.lower() == "bearer" and token else None
    return None


class McpGateway:
    """The ASGI app of ``/mcp``. Serves only while ``running()``: it holds the HTTP client."""

    def __init__(self, config: McpGatewayConfig) -> None:
        self._config = config
        self._cache = TokenCache()
        self._refusals = RefusalCache()
        self._server, self._tools = build_server()
        self._session_manager: StreamableHTTPSessionManager | None = None
        self._client: MyspaceExtClient | None = None
        self._broker: TokenBroker | None = None

    @asynccontextmanager
    async def running(
        self, transport: httpx.AsyncBaseTransport | None = None
    ) -> AsyncIterator[None]:
        """Inside the app lifespan. ``transport`` replaces the network in tests."""
        # A session manager runs once; each start makes a new one.
        self._server.streamable_http_app(
            streamable_http_path=MCP_PATH,
            stateless_http=True,
            json_response=True,
            transport_security=_NO_HOST_CHECK,
        )
        session_manager = self._server.session_manager
        async with (
            httpx.AsyncClient(timeout=TIMEOUT, transport=transport) as http,
            session_manager.run(),
        ):
            if self._config.is_configured:
                self._client = MyspaceExtClient(
                    http,
                    self._config.myspace_ext_api_url,
                    self._config.client_id,
                    self._config.client_secret,
                )
                self._broker = TokenBroker(
                    self._client, self._cache, self._refusals, self._tools.scopes
                )
            else:
                logger.warning(
                    "mcp_gateway: MYSPACE_EXT_API_URL or MCP_GATEWAY_CLIENT_SECRET is not set, "
                    "or the address is not https:// on prod; /mcp answers 503"
                )
            self._session_manager = session_manager
            try:
                yield
            finally:
                self._session_manager = self._client = self._broker = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        session_manager, client, broker = self._session_manager, self._client, self._broker
        if session_manager is None or client is None or broker is None:
            await JSONResponse(_NOT_CONFIGURED_BODY, status_code=503)(scope, receive, send)
            return
        token = _bearer_token(scope)
        if token is None or not looks_like_access_token(token):
            await JSONResponse(_INVALID_TOKEN_BODY, 401, _INVALID_TOKEN_HEADERS)(
                scope, receive, send
            )
            return
        key = token_key(token)
        deadline = request_deadline()
        try:
            async with asyncio.timeout_at(deadline):
                grant = await broker.grant_for(token, key)
        except ExchangeRefused as exc:
            logger.info(
                "mcp_gateway: MySpace refused caller {caller}: {status} {code}",
                caller=log_id(key),
                status=exc.status,
                code=exc.code,
            )
            if isinstance(exc, ExchangeScopeRefused):
                response = JSONResponse(_INSUFFICIENT_SCOPE_BODY, 403, _INSUFFICIENT_SCOPE_HEADERS)
            else:
                response = JSONResponse(_INVALID_TOKEN_BODY, 401, _INVALID_TOKEN_HEADERS)
            await response(scope, receive, send)
            return
        except MyspaceUnavailable as exc:
            log_unavailable(exc)
            await JSONResponse(_UNAVAILABLE_BODY, 503, _UNAVAILABLE_HEADERS)(scope, receive, send)
            return
        except TimeoutError:
            logger.warning("mcp_gateway: token exchange ran past the request deadline")
            await JSONResponse(_UNAVAILABLE_BODY, 503, _UNAVAILABLE_HEADERS)(scope, receive, send)
            return
        scope.setdefault("state", {})[CALLER_STATE_KEY] = CallerSession(
            client=client,
            broker=broker,
            subject_token=token,
            key=key,
            grant=grant,
            deadline=deadline,
        )
        await session_manager.handle_request(scope, receive, send)
