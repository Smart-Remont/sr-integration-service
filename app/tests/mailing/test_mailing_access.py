"""The caller check and the configuration of each cron."""

import pytest
from httpx import AsyncClient

from src.config import mailing_config, mailing_cron_auth_config
from tests.mailing.fakes import CRON_USER, Database, MailServer, basic

REMOVED_ROUTES = [
    "/api/mailing/send-email-error-db",
    "/api/mailing/provider-send-email-requests",
]
ROUTES = [
    "/api/mailing/fill-send-email",
    "/api/mailing/send-email-new",
    "/api/mailing/paid-client-request-to-remont",
]
QUEUE_SETTINGS = [
    "smtp_host",
    "smtp_port",
    "smtp_username",
    "smtp_password",
    "from_name",
    "link_base_url",
    "expeditor_cc_address",
    "office_public_dir",
]


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": basic(CRON_USER, "wrong")}, {"Authorization": basic("wrong", "x")}],
)
async def test_wrong_caller_is_refused_before_anything_runs(
    client: AsyncClient,
    db: Database,
    mail: MailServer,
    route: str,
    headers: dict[str, str],
) -> None:
    client.headers.pop("Authorization")

    response = await client.get(route, headers=headers)

    assert response.status_code == 401
    assert db.events == []
    assert mail.settings == []


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("field", ["username", "password"])
async def test_missing_cron_credentials_are_503(
    client: AsyncClient,
    db: Database,
    mail: MailServer,
    monkeypatch: pytest.MonkeyPatch,
    route: str,
    field: str,
) -> None:
    monkeypatch.setattr(mailing_cron_auth_config, field, "")

    response = await client.get(route)

    assert response.status_code == 503
    assert db.events == []
    assert mail.settings == []


@pytest.mark.parametrize("field", QUEUE_SETTINGS)
async def test_queue_without_a_setting_is_503_and_does_nothing(
    client: AsyncClient,
    db: Database,
    mail: MailServer,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
) -> None:
    monkeypatch.setattr(mailing_config, field, 0 if field == "smtp_port" else "")

    response = await client.get("/api/mailing/send-email-new")

    assert response.status_code == 503
    assert db.events == []
    assert mail.settings == []


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH", "HEAD"])
async def test_any_method_and_zend_tail_work_as_in_php(
    client: AsyncClient, route: str, method: str
) -> None:
    response = await client.request(method, f"{route}/x/1")

    assert response.status_code == 200


@pytest.mark.parametrize("route", REMOVED_ROUTES)
async def test_dead_crons_are_not_moved(client: AsyncClient, db: Database, route: str) -> None:
    response = await client.get(route)

    assert response.status_code == 404
    assert db.events == []
