"""Tools tied to a MySpace scope: registration, the scope filter of ``tools/list``, the reads.

Every tool belongs to exactly one scope (``kanban.read``, ...). A caller sees in ``tools/list``
only the tools of the scopes MySpace gave his exchanged token. A call of any other tool is refused
by the server middleware before the tool runs, as if the tool did not exist, and ``read`` checks
the scope once more. A tool added past ``ScopedTools`` has no scope: hidden and refused.
"""

import asyncio
from collections.abc import Callable
from typing import Any

from loguru import logger
from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_PARAMS, ToolAnnotations

from .client import (
    UNAVAILABLE_TEXT,
    ExchangeRefused,
    ExchangeScopeRefused,
    ExtRequestFailed,
    ExtTokenRejected,
    MyspaceUnavailable,
    log_unavailable,
)
from .session import CallerSession, caller_of

READ_ONLY = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)
SCOPE_META_KEY = "smartremont/scope"

ACCESS_ENDED_TEXT = (
    "Доступ к MySpace от имени сотрудника закончился или отозван. "
    "Нужно заново подключить MySpace в ассистенте."
)


def scope_denied_text(scope: str) -> str:
    return f"Нет доступа: у токена нет области «{scope}»."


class ScopedTools:
    """Tool name -> its scope. Filled once while the server is built."""

    def __init__(self) -> None:
        self._scope_of: dict[str, str] = {}

    @property
    def scopes(self) -> frozenset[str]:
        return frozenset(self._scope_of.values())

    def scope_of(self, tool_name: str) -> str | None:
        return self._scope_of.get(tool_name)

    def add(
        self,
        server: MCPServer,
        fn: Callable[..., Any],
        *,
        scope: str,
        title: str,
        description: str,
    ) -> None:
        """A read-only tool of ``scope``, named after the function, with structured output."""
        name = fn.__name__
        server.add_tool(
            fn,
            name=name,
            title=title,
            description=description,
            annotations=READ_ONLY,
            meta={SCOPE_META_KEY: scope},
            structured_output=True,
        )
        self._scope_of[name] = scope

    async def enforce_scopes(
        self, ctx: ServerRequestContext[Any, Any], call_next: CallNext
    ) -> HandlerResult:
        """Server middleware: a call outside the caller's scopes never runs, a list hides it."""
        caller = caller_of(ctx.request)
        granted = caller.scopes if caller is not None else frozenset()
        if ctx.method == "tools/call":
            tool_name = str((ctx.params or {}).get("name") or "")
            if self.scope_of(tool_name) not in granted:
                raise MCPError(code=INVALID_PARAMS, message=f"Unknown tool: {tool_name}")
        result = await call_next(ctx)
        if ctx.method != "tools/list":
            return result
        return self._visible_tools(result, granted)

    def _visible_tools(self, result: HandlerResult, granted: frozenset[str]) -> HandlerResult:
        tools = result.get("tools") if isinstance(result, dict) else None
        if not isinstance(tools, list):
            # Not the shape this filter knows (an SDK change): show nothing rather than everything.
            logger.error("mcp_gateway: tools/list result of unknown shape, answering no tools")
            return {**result, "tools": []} if isinstance(result, dict) else {"tools": []}
        visible = [
            tool
            for tool in tools
            if isinstance(tool, dict) and self.scope_of(str(tool.get("name"))) in granted
        ]
        return {**result, "tools": visible}

    async def read(
        self, ctx: Context, path: str, params: dict[str, str] | None = None
    ) -> dict[str, Any]:
        """GET ``/ext/v1/<path>`` as the caller of this tool call, its errors as tool errors."""
        caller, scope = self._caller_allowed_to_call(ctx)
        try:
            async with asyncio.timeout_at(caller.deadline):
                return await caller.read(path, params)
        except ExtRequestFailed as exc:
            if exc.status == 429 and exc.retry_after:
                raise ToolError(f"{exc.message} (Retry-After: {exc.retry_after} с)") from exc
            raise ToolError(exc.message) from exc
        except ExchangeScopeRefused:
            raise ToolError(scope_denied_text(scope)) from None
        except ExtTokenRejected, ExchangeRefused:
            logger.info(
                "mcp_gateway: caller {caller} lost access on {path}",
                caller=caller.log_id,
                path=path,
            )
            raise ToolError(ACCESS_ENDED_TEXT) from None
        except MyspaceUnavailable as exc:
            log_unavailable(exc)
            raise ToolError(UNAVAILABLE_TEXT) from None
        except TimeoutError:
            logger.warning("mcp_gateway: {path} ran past the request deadline", path=path)
            raise ToolError(UNAVAILABLE_TEXT) from None

    def _caller_allowed_to_call(self, ctx: Context) -> tuple[CallerSession, str]:
        caller = caller_of(ctx.request_context.request)
        scope = self.scope_of(_tool_name(ctx))
        if caller is None or scope is None:
            # Not through the gate, or a tool registered past ScopedTools: never served.
            raise ToolError(ACCESS_ENDED_TEXT)
        if scope not in caller.scopes:
            raise ToolError(scope_denied_text(scope))
        return caller, scope


def _tool_name(ctx: Context) -> str:
    return str((ctx.request_context.params or {}).get("name") or "")
