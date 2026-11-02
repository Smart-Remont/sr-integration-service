"""SMS crons: the caller check and the configuration they need."""

import pytest
from httpx import AsyncClient

from src.config import kcell_config, sms_cron_auth_config
from tests.sms.fakes import CRON_USER, NOTIFY_ROUTE, STATUS_ROUTE, Database, Kcell, basic


@pytest.mark.parametrize("route", [NOTIFY_ROUTE, STATUS_ROUTE])
@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": basic(CRON_USER, "wrong")}, {"Authorization": basic("wrong", "x")}],
)
async def test_wrong_caller_is_refused_before_anything_runs(
    client: AsyncClient,
    db: Database,
    kcell: Kcell,
    route: str,
    headers: dict[str, str],
) -> None:
    client.headers.pop("Authorization")

    response = await client.get(route, headers=headers)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Basic"
    assert db.events == []
    assert kcell.requests == []


@pytest.mark.parametrize("route", [NOTIFY_ROUTE, STATUS_ROUTE])
@pytest.mark.parametrize("field", ["username", "password"])
async def test_missing_cron_credentials_are_503(
    client: AsyncClient,
    db: Database,
    kcell: Kcell,
    monkeypatch: pytest.MonkeyPatch,
    route: str,
    field: str,
) -> None:
    monkeypatch.setattr(sms_cron_auth_config, field, "")

    response = await client.get(route)

    assert response.status_code == 503
    assert db.events == []
    assert kcell.requests == []


@pytest.mark.parametrize(
    ("route", "field"),
    [
        (NOTIFY_ROUTE, "send_url"),
        (NOTIFY_ROUTE, "send_username"),
        (NOTIFY_ROUTE, "send_password"),
        (STATUS_ROUTE, "status_url"),
        (STATUS_ROUTE, "status_username"),
        (STATUS_ROUTE, "status_password"),
    ],
)
async def test_missing_kcell_config_is_503_before_the_database(
    client: AsyncClient,
    db: Database,
    kcell: Kcell,
    monkeypatch: pytest.MonkeyPatch,
    route: str,
    field: str,
) -> None:
    monkeypatch.setattr(kcell_config, field, "")

    response = await client.get(route)

    assert response.status_code == 503
    assert db.events == []
    assert kcell.requests == []


@pytest.mark.parametrize(
    ("route", "other_field"),
    [(NOTIFY_ROUTE, "status_url"), (STATUS_ROUTE, "send_url")],
)
async def test_each_route_needs_only_its_own_kcell_config(
    client: AsyncClient,
    kcell: Kcell,
    monkeypatch: pytest.MonkeyPatch,
    route: str,
    other_field: str,
) -> None:
    monkeypatch.setattr(kcell_config, other_field, "")

    response = await client.get(route)

    assert response.status_code == 200
    assert len(kcell.requests) == 1
