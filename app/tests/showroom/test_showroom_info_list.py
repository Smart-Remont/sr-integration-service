"""``showroom-info-list``, moved from PHP. BI Group is a mock: its real answer has not been seen."""

import httpx
import pytest
from asyncpg.exceptions import DivisionByZeroError, RaiseError
from httpx import AsyncClient

from src.config import showroom_config, showroom_cron_auth_config
from tests.showroom.conftest import BI_URL, CRON_USER, FUNCTION, ROUTE, BiGroup, ShowroomConnection


def _basic(user: str, password: str) -> str:
    return httpx.BasicAuth(user, password)._auth_header


async def test_list_goes_to_bi_group_and_its_answer_comes_back(
    client: AsyncClient, bi_group: BiGroup
) -> None:
    response = await client.get(ROUTE)

    assert response.status_code == 200
    assert response.content == b'{"ok":true}'
    assert response.headers["content-type"] == "text/html; charset=UTF-8"
    assert response.headers["cache-control"] == "no-store, no-cache, must-revalidate"
    request = bi_group.requests[0]
    assert request.method == "POST"
    assert str(request.url) == BI_URL
    assert request.content == b'{"SMART_REMONT_SHOW_ROOMS":[]}'
    assert request.headers["content-type"] == "application/json"
    assert "user-agent" not in request.headers


async def test_no_database_connection_is_held_while_bi_group_answers(
    client: AsyncClient, bi_group: BiGroup
) -> None:
    await client.get(ROUTE)

    assert bi_group.connections_out == [0]


@pytest.mark.parametrize("status", [404, 500, 503])
async def test_any_bi_group_status_is_200_with_its_body(
    client: AsyncClient, bi_group: BiGroup, status: int
) -> None:
    bi_group.status = status
    bi_group.body = b"<html>error</html>"

    response = await client.get(ROUTE)

    assert response.status_code == 200
    assert response.content == b"<html>error</html>"


@pytest.mark.parametrize(
    "error",
    [httpx.ConnectError("x"), httpx.ReadTimeout("x"), httpx.RemoteProtocolError("x")],
)
async def test_no_answer_is_an_empty_200(
    client: AsyncClient, bi_group: BiGroup, error: Exception
) -> None:
    bi_group.error = error

    response = await client.get(ROUTE)

    assert response.status_code == 200
    assert response.content == b""


@pytest.mark.parametrize("failure", [DivisionByZeroError("x"), RaiseError("{x}")])
async def test_failed_function_posts_an_empty_body_as_php(
    client: AsyncClient, db: ShowroomConnection, bi_group: BiGroup, failure: Exception
) -> None:
    db.responses[FUNCTION] = failure

    response = await client.get(ROUTE)

    assert response.status_code == 200
    assert bi_group.requests[0].content == b""


async def test_null_list_posts_an_empty_body(
    client: AsyncClient, db: ShowroomConnection, bi_group: BiGroup
) -> None:
    db.responses[FUNCTION] = [{"result": None}]

    await client.get(ROUTE)

    assert bi_group.requests[0].content == b""


async def test_no_database_sends_nothing(
    client: AsyncClient, db: ShowroomConnection, bi_group: BiGroup
) -> None:
    db.responses[FUNCTION] = OSError("down")

    with pytest.raises(OSError, match="down"):
        await client.get(ROUTE)

    assert bi_group.requests == []


@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "DELETE", "PATCH"])
@pytest.mark.parametrize("tail", ["", "/", "/x/1"])
async def test_any_method_and_address_form_as_in_zend(
    client: AsyncClient, bi_group: BiGroup, method: str, tail: str
) -> None:
    response = await client.request(method, f"{ROUTE}{tail}")

    assert response.status_code == 200
    assert len(bi_group.requests) == 1


@pytest.mark.parametrize(
    "headers",
    [{"Authorization": ""}, {"Authorization": _basic(CRON_USER, "wrong")}],
)
async def test_wrong_caller_is_refused_and_nothing_runs(
    client: AsyncClient, db: ShowroomConnection, bi_group: BiGroup, headers: dict[str, str]
) -> None:
    response = await client.get(ROUTE, headers=headers)

    assert response.status_code == 401
    assert db.calls == []
    assert bi_group.requests == []


@pytest.mark.parametrize(
    ("config", "field"),
    [
        (showroom_cron_auth_config, "username"),
        (showroom_cron_auth_config, "password"),
        (showroom_config, "bi_url"),
    ],
)
async def test_empty_setting_is_503_and_nothing_runs(
    client: AsyncClient,
    db: ShowroomConnection,
    bi_group: BiGroup,
    monkeypatch: pytest.MonkeyPatch,
    config: object,
    field: str,
) -> None:
    monkeypatch.setattr(config, field, "")

    response = await client.get(ROUTE)

    assert response.status_code == 503
    assert db.calls == []
    assert bi_group.requests == []
