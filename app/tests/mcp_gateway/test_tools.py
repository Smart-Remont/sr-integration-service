"""Kanban tools: the scope filter, arguments -> /ext/v1 URL and query, MySpace errors -> isError."""

import asyncio
from collections.abc import Iterator
from typing import Any

import httpx
import httpx2
import pytest
from loguru import logger
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client

from src.features.mcp_gateway import session
from src.features.mcp_gateway.client import UNAVAILABLE_TEXT, IssuedToken, MyspaceExtClient
from src.features.mcp_gateway.deps import mcp_gateway
from src.features.mcp_gateway.tokens import RefusalCache, TokenBroker, TokenCache
from src.features.mcp_gateway.tooling import ACCESS_ENDED_TEXT
from src.main import app
from tests.mcp_gateway.conftest import (
    CALLER_TOKEN,
    MYSPACE_TOKEN,
    FakeMyspace,
    exchange_form,
    open_mcp,
    serve,
    srx,
)

KANBAN_TOOLS = {
    "kanban_list_boards",
    "kanban_get_board",
    "kanban_list_cards",
    "kanban_get_card",
    "kanban_list_overdue",
}
CARD = {"card": {"id": 88088, "title": "Замер", "url": "/kanban/board?boardId=64&cardId=88088"}}

pytestmark = pytest.mark.usefixtures("configured")


def _text(result: dict[str, Any]) -> str:
    return " ".join(block["text"] for block in result["content"] if block["type"] == "text")


def _error_text(result: dict[str, Any], tool: str) -> str:
    """The SDK puts «Error executing tool <name>: » before the text of the tool."""
    assert result["isError"] is True
    prefix = f"Error executing tool {tool}: "
    text = _text(result)
    assert text.startswith(prefix), text
    return text.removeprefix(prefix)


async def test_initialize_answers_without_session(myspace: FakeMyspace) -> None:
    async with open_mcp(myspace) as mcp:
        response = await mcp.post(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert "mcp-session-id" not in response.headers
    result = response.json()["result"]
    assert result["serverInfo"]["name"] == "smartremont-myspace"
    assert "tools" in result["capabilities"]


async def test_tools_list_shows_kanban_tools_read_only(myspace: FakeMyspace) -> None:
    async with open_mcp(myspace) as mcp:
        tools = (await mcp.result("tools/list"))["tools"]

    assert {tool["name"] for tool in tools} == KANBAN_TOOLS
    for tool in tools:
        assert tool["annotations"]["readOnlyHint"] is True
        assert tool["annotations"]["openWorldHint"] is False
        assert tool["_meta"]["smartremont/scope"] == "kanban.read"
        assert tool["description"]
        assert tool["outputSchema"]["type"] == "object"
    cards = next(tool for tool in tools if tool["name"] == "kanban_list_cards")
    schema = cards["inputSchema"]
    assert schema["required"] == ["board_id"]
    assert all(prop["description"] for prop in schema["properties"].values())


async def test_tools_list_hides_tools_of_scopes_not_given(myspace: FakeMyspace) -> None:
    myspace.exchange_answers.append(myspace.issue(scope="requests.read"))

    async with open_mcp(myspace) as mcp:
        tools = (await mcp.result("tools/list"))["tools"]

    assert tools == []


def _rpc_error(response: httpx.Response) -> dict[str, Any]:
    assert response.status_code == 200
    body = response.json()
    assert "result" not in body, body
    return body["error"]


async def test_call_of_a_tool_outside_the_scopes_is_refused(myspace: FakeMyspace) -> None:
    myspace.exchange_answers.append(myspace.issue(scope="requests.read"))

    async with open_mcp(myspace) as mcp:
        response = await mcp.post("tools/call", {"name": "kanban_list_boards", "arguments": {}})

    error = _rpc_error(response)
    assert error["code"] == -32602
    assert error["message"] == "Unknown tool: kanban_list_boards"
    assert myspace.data_reads == []


@pytest.fixture
def tool_without_scope() -> Iterator[list[str]]:
    """Added straight to the SDK server, past ScopedTools: what a careless new tool would be."""
    ran: list[str] = []

    async def rogue_dump() -> dict[str, Any]:
        ran.append("ran")
        return {"secret": "everything"}

    mcp_gateway._server.add_tool(rogue_dump)
    yield ran
    mcp_gateway._server.remove_tool("rogue_dump")


async def test_tool_without_a_scope_is_hidden_and_never_runs(
    myspace: FakeMyspace, tool_without_scope: list[str]
) -> None:
    async with open_mcp(myspace) as mcp:
        tools = (await mcp.result("tools/list"))["tools"]
        response = await mcp.post("tools/call", {"name": "rogue_dump", "arguments": {}})

    assert "rogue_dump" not in {tool["name"] for tool in tools}
    assert _rpc_error(response)["code"] == -32602
    assert tool_without_scope == []


async def test_answer_is_myspace_json_as_structured_content(myspace: FakeMyspace) -> None:
    myspace.reads["/ext/v1/kanban/cards/88088/"] = httpx.Response(200, json=CARD)

    async with open_mcp(myspace) as mcp:
        result = await mcp.call("kanban_get_card", {"card_id": 88088})

    assert result.get("isError") is not True
    assert result["structuredContent"] == CARD
    (read,) = myspace.data_reads
    assert read.method == "GET"
    assert read.headers["authorization"] == f"Bearer {MYSPACE_TOKEN}"


@pytest.mark.parametrize(
    ("tool", "arguments", "path", "query"),
    [
        ("kanban_list_boards", {}, "/ext/v1/kanban/boards/", {}),
        ("kanban_get_board", {"board_id": 64}, "/ext/v1/kanban/boards/64/", {}),
        ("kanban_get_card", {"card_id": 88088}, "/ext/v1/kanban/cards/88088/", {}),
        ("kanban_list_overdue", {"board_id": 64}, "/ext/v1/kanban/boards/64/overdue/", {}),
        (
            "kanban_list_cards",
            {"board_id": 64},
            "/ext/v1/kanban/boards/64/cards/",
            {"limit": "50", "offset": "0"},
        ),
        (
            "kanban_list_cards",
            {
                "board_id": 64,
                "column_id": 7,
                "mine": True,
                "overdue": True,
                "executor_id": 1457,
                "search": "  замер  ",
                "include_all_done": True,
                "limit": 200,
                "offset": 100,
            },
            "/ext/v1/kanban/boards/64/cards/",
            {
                "column_id": "7",
                "mine": "1",
                "overdue": "1",
                "executor_id": "1457",
                "search": "замер",
                "all_done": "1",
                "limit": "200",
                "offset": "100",
            },
        ),
        (
            "kanban_list_cards",
            {"board_id": 64, "mine": False, "overdue": False, "search": "  "},
            "/ext/v1/kanban/boards/64/cards/",
            {"limit": "50", "offset": "0"},
        ),
    ],
)
async def test_arguments_become_the_myspace_url(
    myspace: FakeMyspace,
    tool: str,
    arguments: dict[str, Any],
    path: str,
    query: dict[str, str],
) -> None:
    myspace.reads[path] = httpx.Response(200, json={"ok": True})

    async with open_mcp(myspace) as mcp:
        result = await mcp.call(tool, arguments)

    assert result["structuredContent"] == {"ok": True}
    (read,) = myspace.data_reads
    assert read.url.path == path
    assert dict(read.url.params) == query


@pytest.mark.parametrize(
    "arguments",
    [
        {"board_id": 64, "limit": 500},
        {"board_id": 0},
        {"board_id": 2_147_483_648},
        {"board_id": 64, "column_id": 2_147_483_648},
        {"board_id": 64, "executor_id": 2_147_483_648},
        {"board_id": 64, "offset": -1},
        {},
    ],
)
async def test_bad_arguments_never_reach_myspace(
    myspace: FakeMyspace, arguments: dict[str, Any]
) -> None:
    async with open_mcp(myspace) as mcp:
        result = await mcp.call("kanban_list_cards", arguments)

    assert result["isError"] is True
    assert myspace.data_reads == []


@pytest.mark.parametrize(
    ("status", "message"),
    [
        (403, "Нет доступа к доске"),
        (404, "Карточка не найдена или нет доступа"),
        (400, "Параметр «limit» должен быть целым положительным числом"),
    ],
)
async def test_myspace_refusal_is_a_tool_error_with_its_text(
    myspace: FakeMyspace, status: int, message: str
) -> None:
    myspace.reads["/ext/v1/kanban/cards/88088/"] = httpx.Response(
        status, json={"error": "forbidden", "message": message}
    )

    async with open_mcp(myspace) as mcp:
        result = await mcp.call("kanban_get_card", {"card_id": 88088})

    assert _error_text(result, "kanban_get_card") == message


async def test_rate_limit_carries_retry_after(myspace: FakeMyspace) -> None:
    myspace.reads["/ext/v1/kanban/boards/"] = httpx.Response(
        429,
        json={"error": "rate_limited", "message": "Слишком много запросов, повторите через минуту"},
        headers={"Retry-After": "42"},
    )

    async with open_mcp(myspace) as mcp:
        result = await mcp.call("kanban_list_boards")

    assert result["isError"] is True
    assert "Слишком много запросов" in _text(result)
    assert "Retry-After: 42" in _text(result)


@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(500, json={"error": "server_error"}),
        httpx.Response(502, text="bad gateway"),
        httpx.Response(200, text="not json"),
        httpx.ReadTimeout("slow"),
    ],
)
async def test_myspace_down_on_a_call_is_unavailable(
    myspace: FakeMyspace, answer: httpx.Response | Exception
) -> None:
    def read(request: httpx.Request) -> httpx.Response:
        if isinstance(answer, Exception):
            raise answer
        return answer

    myspace.reads["/ext/v1/kanban/boards/"] = read

    async with open_mcp(myspace) as mcp:
        result = await mcp.call("kanban_list_boards")

    assert _error_text(result, "kanban_list_boards") == UNAVAILABLE_TEXT


async def test_401_on_a_call_exchanges_once_more_and_retries(myspace: FakeMyspace) -> None:
    def read(request: httpx.Request) -> httpx.Response:
        if request.headers["authorization"] == f"Bearer {MYSPACE_TOKEN}":
            return httpx.Response(401, json={"error": "invalid_token"})
        return httpx.Response(200, json={"boards": []})

    myspace.reads["/ext/v1/kanban/boards/"] = read

    async with open_mcp(myspace) as mcp:
        first = await mcp.call("kanban_list_boards")
        second = await mcp.call("kanban_list_boards")

    assert first["structuredContent"] == {"boards": []}
    assert second["structuredContent"] == {"boards": []}
    # The new token replaced the dead one in the cache: no third exchange.
    assert len(myspace.exchanges) == 2
    assert [r.headers["authorization"] for r in myspace.data_reads] == [
        f"Bearer {MYSPACE_TOKEN}",
        f"Bearer {MYSPACE_TOKEN}_2",
        f"Bearer {MYSPACE_TOKEN}_2",
    ]


async def test_401_twice_is_a_tool_error(myspace: FakeMyspace) -> None:
    myspace.reads["/ext/v1/kanban/boards/"] = httpx.Response(401, json={"error": "invalid_token"})

    async with open_mcp(myspace) as mcp:
        result = await mcp.call("kanban_list_boards")

    assert _error_text(result, "kanban_list_boards") == ACCESS_ENDED_TEXT
    assert len(myspace.exchanges) == 2
    assert len(myspace.data_reads) == 2


async def test_parent_revoked_then_401_and_refused_exchange(myspace: FakeMyspace) -> None:
    myspace.reads["/ext/v1/kanban/boards/"] = httpx.Response(401, json={"error": "invalid_token"})
    revoked = httpx.Response(400, json={"error": "invalid_grant"})
    myspace.exchange_answers += [myspace.issue(), revoked, revoked]

    async with open_mcp(myspace) as mcp:
        result = await mcp.call("kanban_list_boards")
        # The dead token left the cache and the refusal is remembered: 401 without MySpace.
        next_request = await mcp.post("tools/list")

    assert _error_text(result, "kanban_list_boards") == ACCESS_ENDED_TEXT
    assert next_request.status_code == 401
    assert len(myspace.data_reads) == 1
    assert len(myspace.exchanges) == 2


async def test_unknown_tool_is_an_error(myspace: FakeMyspace) -> None:
    async with open_mcp(myspace) as mcp:
        response = await mcp.post("tools/call", {"name": "knb_direct_sql", "arguments": {}})

    assert _rpc_error(response)["code"] == -32602
    assert myspace.data_reads == []


async def test_each_caller_reads_with_his_own_token(myspace: FakeMyspace) -> None:
    second = srx("second")
    myspace.reads["/ext/v1/kanban/boards/"] = httpx.Response(200, json={"boards": []})

    async with open_mcp(myspace) as mcp:
        await mcp.call("kanban_list_boards")
        await mcp.call("kanban_list_boards", token=second)
        await mcp.call("kanban_list_boards")

    assert [exchange_form(r)["subject_token"] for r in myspace.exchanges] == [CALLER_TOKEN, second]
    assert [r.headers["authorization"] for r in myspace.data_reads] == [
        f"Bearer {MYSPACE_TOKEN}",
        f"Bearer {MYSPACE_TOKEN}_2",
        f"Bearer {MYSPACE_TOKEN}",
    ]


async def test_refresh_keeps_a_token_another_request_already_renewed(
    myspace: FakeMyspace,
) -> None:
    cache = TokenCache()
    async with httpx.AsyncClient(transport=httpx.MockTransport(myspace.handle)) as http:
        client = MyspaceExtClient(http, "https://myspace.test", "mcp-gateway", "secret")
        broker = TokenBroker(client, cache, RefusalCache(), frozenset({"kanban.read"}))
        cache.put("k", IssuedToken("srx_renewed", frozenset({"kanban.read"}), expires_in=600))

        kept = await broker.refresh(CALLER_TOKEN, "k", dead_access_token="srx_dead")
        renewed = await broker.refresh(CALLER_TOKEN, "k", dead_access_token="srx_renewed")

    assert kept.access_token == "srx_renewed"
    assert renewed.access_token == MYSPACE_TOKEN
    assert len(myspace.exchanges) == 1


async def test_slow_read_past_the_deadline_is_unavailable(
    myspace: FakeMyspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(session, "CALL_DEADLINE_SECONDS", 0.2)

    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(1.0)
        return httpx.Response(200, json={"boards": []})

    myspace.reads["/ext/v1/kanban/boards/"] = slow

    async with open_mcp(myspace) as mcp:
        result = await mcp.call("kanban_list_boards")

    assert _error_text(result, "kanban_list_boards") == UNAVAILABLE_TEXT


async def test_refusal_without_json_is_logged(myspace: FakeMyspace) -> None:
    lines: list[str] = []
    sink = logger.add(lambda message: lines.append(str(message)), level="WARNING")
    myspace.reads["/ext/v1/kanban/boards/"] = httpx.Response(403, text="<html>Forbidden</html>")
    try:
        async with open_mcp(myspace) as mcp:
            result = await mcp.call("kanban_list_boards")
    finally:
        logger.remove(sink)

    assert _error_text(result, "kanban_list_boards") == "MySpace отклонил запрос"
    assert any("HTTP 403 without JSON" in line for line in lines)
    assert not any("Forbidden" in line for line in lines)


@pytest.mark.parametrize("mode", ["legacy", "auto"])
async def test_sdk_client_lists_and_calls(myspace: FakeMyspace, mode: str) -> None:
    """The official client, both eras: the initialize handshake and the 2026 per-request one."""
    myspace.reads["/ext/v1/kanban/boards/"] = httpx.Response(200, json={"boards": []})

    async with (
        serve(myspace.handle),
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": f"Bearer {CALLER_TOKEN}"},
        ) as http,
        Client(streamable_http_client("http://test/mcp", http_client=http), mode=mode) as client,
    ):
        tools = await client.list_tools()
        result = await client.call_tool("kanban_list_boards", {})

    assert {tool.name for tool in tools.tools} == KANBAN_TOOLS
    assert result.is_error is not True
    assert result.structured_content == {"boards": []}
