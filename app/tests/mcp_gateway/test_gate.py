"""The Bearer gate of /mcp: exchange at MySpace, the caches, 401, 403 and 503."""

import asyncio
import base64
import logging
from collections.abc import Iterator

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from loguru import logger

from src.config import app_config, mcp_gateway_config
from src.features.mcp_gateway import session
from src.features.mcp_gateway.client import ExchangeRefused, IssuedToken
from src.features.mcp_gateway.tokens import (
    EXPIRY_MARGIN_SECONDS,
    REFUSAL_TTL_SECONDS,
    RefusalCache,
    TokenCache,
)
from src.main import app
from tests.mcp_gateway.conftest import (
    CALLER_TOKEN,
    GATEWAY_CLIENT_ID,
    GATEWAY_SECRET,
    MYSPACE_TOKEN,
    MYSPACE_URL,
    FakeMyspace,
    Mcp,
    exchange_form,
    open_mcp,
    serve,
    srx,
)

BOARDS = {"boards": [{"id": 64, "name": "Доска", "owner": None, "url": "/kanban/board?boardId=64"}]}

pytestmark = pytest.mark.usefixtures("configured")


@pytest.fixture
def log_lines() -> Iterator[list[str]]:
    lines: list[str] = []
    sink = logger.add(lambda message: lines.append(str(message)), level="DEBUG")
    yield lines
    logger.remove(sink)


def _assert_invalid_token(response: httpx.Response) -> None:
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == 'Bearer error="invalid_token"'
    assert response.json()["error"] == "invalid_token"


async def test_no_token_is_401_and_nothing_goes_to_myspace(myspace: FakeMyspace) -> None:
    async with open_mcp(myspace) as mcp:
        response = await mcp.post("tools/list", token=None)

    _assert_invalid_token(response)
    assert myspace.requests == []


async def test_basic_instead_of_bearer_is_401(myspace: FakeMyspace) -> None:
    async with open_mcp(myspace) as mcp:
        response = await mcp.http.post(
            "/mcp", json={}, headers={"Authorization": "Basic dXNlcjpwYXNz"}
        )

    _assert_invalid_token(response)
    assert myspace.requests == []


@pytest.mark.parametrize(
    "token",
    [
        "abc",
        "srx_",
        "srx_short",
        "srx_" + "a" * 42,
        "srx_" + "a" * 44,
        "srx_" + "a" * 42 + ".",
        "srx_" + "a" * 42 + "=",
        "SRX_" + "a" * 43,
        "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiIxIn0.c2ln",
    ],
)
async def test_token_not_shaped_like_myspace_is_401_without_myspace(
    myspace: FakeMyspace, token: str
) -> None:
    async with open_mcp(myspace) as mcp:
        response = await mcp.post("tools/list", token=token)

    _assert_invalid_token(response)
    assert myspace.requests == []


async def test_invalid_grant_is_401(myspace: FakeMyspace) -> None:
    myspace.exchange_answers.append(httpx.Response(400, json={"error": "invalid_grant"}))

    async with open_mcp(myspace) as mcp:
        response = await mcp.post("tools/list")

    _assert_invalid_token(response)


async def test_invalid_scope_is_403_insufficient_scope(myspace: FakeMyspace) -> None:
    myspace.exchange_answers.append(httpx.Response(400, json={"error": "invalid_scope"}))

    async with open_mcp(myspace) as mcp:
        response = await mcp.post("tools/list")

    assert response.status_code == 403
    assert response.headers["www-authenticate"] == 'Bearer error="insufficient_scope"'
    assert response.json()["error"] == "insufficient_scope"


@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(401, json={"error": "invalid_client"}),
        httpx.Response(400, json={"error": "invalid_target"}),
        httpx.Response(400, json={"error": "unsupported_grant_type"}),
        httpx.Response(400, json={"error": "invalid_request"}),
        httpx.Response(400, json={"error": "something_new"}),
        httpx.Response(400, text="<html>Bad Request</html>"),
        httpx.Response(403, text="Forbidden"),
    ],
)
async def test_refusal_of_the_gateway_itself_is_503_and_an_error_in_the_log(
    myspace: FakeMyspace, answer: httpx.Response, log_lines: list[str]
) -> None:
    myspace.exchange_answers.append(answer)

    async with open_mcp(myspace) as mcp:
        response = await mcp.post("tools/list")

    assert response.status_code == 503
    assert response.json()["error"] == "temporarily_unavailable"
    assert any("| ERROR" in line and "refused the gateway" in line for line in log_lines)


@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(500, text="boom"),
        httpx.Response(503, json={"error": "temporarily_unavailable"}),
        httpx.Response(200, text="<html>not json</html>"),
        httpx.Response(200, json={"error": "no token here"}),
    ],
)
async def test_myspace_failing_the_exchange_is_503(
    myspace: FakeMyspace, answer: httpx.Response
) -> None:
    myspace.exchange_answers.append(answer)

    async with open_mcp(myspace) as mcp:
        response = await mcp.post("tools/list")

    assert response.status_code == 503
    assert response.json()["error"] == "temporarily_unavailable"


async def test_myspace_not_answering_the_exchange_is_503() -> None:
    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    async with serve(unreachable) as http:
        response = await Mcp(http).post("tools/list")

    assert response.status_code == 503


async def test_slow_exchange_past_the_deadline_is_503(
    myspace: FakeMyspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(session, "CALL_DEADLINE_SECONDS", 0.05)
    myspace.exchange_delay = 1.0

    async with open_mcp(myspace) as mcp:
        response = await mcp.post("tools/list")

    assert response.status_code == 503


async def test_exchange_request_is_the_documented_one(myspace: FakeMyspace) -> None:
    async with open_mcp(myspace) as mcp:
        await mcp.result("tools/list")

    (request,) = myspace.exchanges
    assert request.method == "POST"
    assert str(request.url) == f"{MYSPACE_URL}/ext/v1/auth/token/"
    expected = base64.b64encode(f"{GATEWAY_CLIENT_ID}:{GATEWAY_SECRET}".encode()).decode()
    assert request.headers["authorization"] == f"Basic {expected}"
    assert request.headers["content-type"] == "application/x-www-form-urlencoded"
    assert exchange_form(request) == {
        "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
        "subject_token": CALLER_TOKEN,
        "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
        "audience": "myspace-ext",
        "scope": "kanban.read",
    }


async def test_secret_in_basic_is_form_encoded(
    myspace: FakeMyspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mcp_gateway_config, "client_secret", "a:b c")

    async with open_mcp(myspace) as mcp:
        await mcp.result("tools/list")

    header = myspace.exchanges[-1].headers["authorization"].removeprefix("Basic ")
    assert base64.b64decode(header).decode() == f"{GATEWAY_CLIENT_ID}:a%3Ab%20c"


async def test_second_request_uses_the_cache(myspace: FakeMyspace) -> None:
    myspace.reads["/ext/v1/kanban/boards/"] = httpx.Response(200, json=BOARDS)

    async with open_mcp(myspace) as mcp:
        await mcp.result("tools/list")
        await mcp.call("kanban_list_boards")
        await mcp.call("kanban_list_boards")

    assert len(myspace.exchanges) == 1
    assert [r.headers["authorization"] for r in myspace.data_reads] == [
        f"Bearer {MYSPACE_TOKEN}"
    ] * 2


async def test_concurrent_misses_of_one_caller_make_one_exchange(myspace: FakeMyspace) -> None:
    myspace.exchange_delay = 0.05

    async with open_mcp(myspace) as mcp:
        responses = await asyncio.gather(*(mcp.post("tools/list") for _ in range(5)))

    assert [r.status_code for r in responses] == [200] * 5
    assert len(myspace.exchanges) == 1


async def test_each_caller_gets_his_own_exchange(myspace: FakeMyspace) -> None:
    first, second = srx("first"), srx("second")

    async with open_mcp(myspace) as mcp:
        await mcp.post("tools/list", token=first)
        await mcp.post("tools/list", token=second)
        await mcp.post("tools/list", token=first)

    assert [exchange_form(r)["subject_token"] for r in myspace.exchanges] == [first, second]


async def test_refused_token_is_answered_from_memory(myspace: FakeMyspace) -> None:
    myspace.exchange_answers.append(httpx.Response(400, json={"error": "invalid_grant"}))

    async with open_mcp(myspace) as mcp:
        first = await mcp.post("tools/list")
        second = await mcp.post("tools/list")

    _assert_invalid_token(first)
    _assert_invalid_token(second)
    assert len(myspace.exchanges) == 1


async def test_refusal_of_the_gateway_is_not_remembered(myspace: FakeMyspace) -> None:
    myspace.exchange_answers.append(httpx.Response(401, json={"error": "invalid_client"}))

    async with open_mcp(myspace) as mcp:
        assert (await mcp.post("tools/list")).status_code == 503
        assert (await mcp.post("tools/list")).status_code == 200

    assert len(myspace.exchanges) == 2


async def test_not_configured_is_503(myspace: FakeMyspace, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mcp_gateway_config, "client_secret", "")

    async with open_mcp(myspace) as mcp:
        response = await mcp.post("tools/list")

    assert response.status_code == 503
    assert myspace.requests == []


async def test_without_lifespan_is_503() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        response = await Mcp(http).post("tools/list")

    assert response.status_code == 503


async def test_only_exactly_mcp_is_served(myspace: FakeMyspace) -> None:
    async with open_mcp(myspace) as mcp:
        response = await mcp.http.post("/mcp/", headers={"Authorization": f"Bearer {CALLER_TOKEN}"})

    assert response.status_code == 404


def test_prod_refuses_plain_http_to_myspace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mcp_gateway_config, "myspace_ext_api_url", "http://myspace.internal")
    monkeypatch.setattr(app_config, "env", "prod")

    assert not mcp_gateway_config.is_configured


async def test_tokens_and_secret_never_reach_the_log(
    myspace: FakeMyspace, log_lines: list[str], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    refused = srx("refused")
    myspace.reads["/ext/v1/kanban/boards/"] = httpx.Response(401, json={"error": "invalid_token"})
    myspace.exchange_answers += [
        myspace.issue(),
        myspace.issue(),
        httpx.Response(400, json={"error": "invalid_grant"}),
    ]

    async with open_mcp(myspace) as mcp:
        await mcp.call("kanban_list_boards")
        await mcp.post("tools/list", token=refused)

    loguru_text = "\n".join(log_lines)
    assert "mcp_gateway" in loguru_text
    assert "HTTP Request" in caplog.text
    for secret in (CALLER_TOKEN, refused, MYSPACE_TOKEN, f"{MYSPACE_TOKEN}_2", GATEWAY_SECRET):
        assert secret not in loguru_text
        assert secret not in caplog.text


def test_cache_keeps_a_token_until_expiry_minus_margin() -> None:
    now = [1000.0]
    cache = TokenCache(clock=lambda: now[0])
    cache.put("k", IssuedToken("srx_b", frozenset({"kanban.read"}), expires_in=600))

    now[0] += 600 - EXPIRY_MARGIN_SECONDS - 1
    assert cache.get("k") is not None
    now[0] += 1
    assert cache.get("k") is None


def test_cache_does_not_keep_a_token_about_to_end() -> None:
    cache = TokenCache()
    grant = cache.put("k", IssuedToken("srx_b", frozenset(), expires_in=EXPIRY_MARGIN_SECONDS))

    assert grant.access_token == "srx_b"
    assert cache.get("k") is None


def test_cache_drops_the_oldest_when_full() -> None:
    cache = TokenCache(max_entries=3)
    for key in ("a", "b", "c", "d"):
        cache.put(key, IssuedToken(f"srx_{key}", frozenset(), expires_in=600))

    assert len(cache) == 3
    assert cache.get("a") is None
    assert [cache.get(key).access_token for key in ("b", "c", "d")] == ["srx_b", "srx_c", "srx_d"]


def test_refusal_is_remembered_for_its_ttl_only() -> None:
    now = [1000.0]
    refusals = RefusalCache(clock=lambda: now[0], max_entries=2)
    refusals.put("k", ExchangeRefused(400, "invalid_grant"))

    now[0] += REFUSAL_TTL_SECONDS - 1
    assert refusals.get("k") is not None
    now[0] += 1
    assert refusals.get("k") is None


def test_refusals_are_bounded() -> None:
    refusals = RefusalCache(max_entries=2)
    for key in ("a", "b", "c"):
        refusals.put(key, ExchangeRefused(400, "invalid_grant"))

    assert refusals.get("a") is None
    assert refusals.get("b") is not None
    assert refusals.get("c") is not None
