"""Who gets in, and what a ``what`` that was not moved does: nothing."""

import base64

import pytest
from httpx import AsyncClient

from src.config import buh_1c_auth_config
from src.features.buh_1c.errors import ACTION_CONTENT_TYPE
from src.features.buh_1c.whats import NOT_MOVED_WHAT, MovedWhat
from src.main import app
from tests.buh_1c.conftest import PASSWORD, ROUTE, USER, Buh1cConnection, FakePool


def _basic(value: bytes, scheme: str = "Basic ") -> dict[str, str]:
    return {"Authorization": scheme + base64.b64encode(value).decode()}


GOOD = f"{USER}:{PASSWORD}".encode()


@pytest.mark.parametrize(
    "headers",
    [
        {},
        _basic(f"{USER}:wrong".encode()),
        _basic(f"nobody:{PASSWORD}".encode()),
        _basic(f"{USER}:".encode()),
        _basic(f":{PASSWORD}".encode()),
        _basic(b":"),
        _basic(USER.encode()),
        _basic("юзер:пароль".encode()),
        _basic(b"\xff:\xff"),
        _basic(GOOD + b":x"),
        _basic(GOOD, scheme="basic "),
        _basic(GOOD, scheme="BASIC "),
        {"Authorization": "Bearer abc"},
        {"Authorization": "Basic !!!"},
        {"Authorization": "Basic"},
        {"Authorization": ""},
    ],
)
@pytest.mark.parametrize("what", ["lot", "warehouse_guid", "provider", "no_such"])
async def test_wrong_login_is_an_empty_401_before_any_call(
    anonymous: AsyncClient,
    db: Buh1cConnection,
    pool: FakePool,
    headers: dict[str, str],
    what: str,
) -> None:
    response = await anonymous.get(ROUTE, params={"what": what}, headers=headers)

    assert response.status_code == 401
    assert response.content == b""
    assert response.headers["www-authenticate"] == 'Basic realm="Access denied"'
    assert response.headers["content-type"] == ACTION_CONTENT_TYPE
    assert response.headers["cache-control"] == "no-cache, must-revalidate, max-age=0"
    assert pool.acquired == 0
    assert db.calls == []


async def test_second_space_after_the_scheme_passes_as_in_php(
    anonymous: AsyncClient, db: Buh1cConnection
) -> None:
    response = await anonymous.get(
        ROUTE, params={"what": "no_such"}, headers=_basic(GOOD, scheme="Basic  ")
    )

    assert response.status_code == 200


@pytest.mark.parametrize(
    "setting",
    [("", ""), (USER, ""), ("", PASSWORD)],
)
@pytest.mark.parametrize(
    "headers",
    [{}, _basic(GOOD), _basic(b":"), {"Authorization": "Basic !!!"}, {"Authorization": "Bearer x"}],
)
@pytest.mark.parametrize("what", ["lot", "warehouse_guid", "provider"])
async def test_empty_setting_is_503_and_does_nothing(
    anonymous: AsyncClient,
    db: Buh1cConnection,
    pool: FakePool,
    monkeypatch: pytest.MonkeyPatch,
    setting: tuple[str, str],
    headers: dict[str, str],
    what: str,
) -> None:
    monkeypatch.setattr(buh_1c_auth_config, "username", setting[0])
    monkeypatch.setattr(buh_1c_auth_config, "password", setting[1])

    response = await anonymous.post(ROUTE, params={"what": what}, content=b"{}", headers=headers)

    assert response.status_code == 503
    assert pool.acquired == 0
    assert db.calls == []


@pytest.mark.parametrize("what", sorted(NOT_MOVED_WHAT))
@pytest.mark.parametrize("method", ["GET", "POST"])
async def test_not_moved_what_says_so_and_touches_nothing(
    client: AsyncClient,
    db: Buh1cConnection,
    pool: FakePool,
    what: str,
    method: str,
) -> None:
    response = await client.request(method, ROUTE, params={"what": what}, content=b"{}")

    assert response.status_code == 410
    assert response.headers["content-type"] == "application/json"
    assert f"what={what} ".encode() in response.content
    assert pool.acquired == 0
    assert db.calls == []


async def test_not_moved_what_in_the_address_is_refused_too(
    client: AsyncClient, pool: FakePool
) -> None:
    response = await client.get(f"{ROUTE}/what/provider")

    assert response.status_code == 410
    assert pool.acquired == 0


def test_php_has_47_values_of_what_and_each_is_either_moved_or_listed() -> None:
    moved = {str(what) for what in MovedWhat}
    assert moved == {"lot", "lot_guid", "material_guid", "warehouse_guid"}
    assert not moved & NOT_MOVED_WHAT
    assert len(moved) + len(NOT_MOVED_WHAT) == 47


def test_route_is_in_the_docs_as_post_only() -> None:
    operations = app.openapi()["paths"]["/api/buh_1c/buh"]
    assert list(operations) == ["post"]
    assert {"200", "400", "401", "410", "503"} <= set(operations["post"]["responses"])
