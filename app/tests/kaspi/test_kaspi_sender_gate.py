"""``kaspi-request-pay`` is reachable only through nginx, with the proxy secret."""

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.config import kaspi_proxy_config
from src.main import app
from tests.kaspi.fakes import PAY, REQUEST_ROUTE, Database

GATED_REQUESTS = [
    ("GET", REQUEST_ROUTE),
    ("GET", f"{REQUEST_ROUTE}/"),
    ("GET", f"{REQUEST_ROUTE}/command/pay/account/1"),
    ("POST", REQUEST_ROUTE),
    ("HEAD", REQUEST_ROUTE),
    ("PUT", f"{REQUEST_ROUTE}/command/pay"),
]


@pytest.mark.parametrize(("method", "url"), GATED_REQUESTS)
@pytest.mark.parametrize(
    "headers",
    [
        {"X-Kaspi-Proxy-Secret": ""},
        {"X-Kaspi-Proxy-Secret": "wrong"},
        {"X-Kaspi-Proxy-Secret": "ключ".encode()},
    ],
)
async def test_request_without_the_right_secret_is_forbidden_before_anything_runs(
    client: AsyncClient,
    db: Database,
    method: str,
    url: str,
    headers: dict[str, Any],
) -> None:
    response = await client.request(method, url, headers=headers, params=PAY)

    assert response.status_code == 403
    assert db.events == []
    assert db.request.calls == []


async def test_missing_secret_header_is_forbidden(db: Database) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        response = await http.get(
            REQUEST_ROUTE,
            params=PAY,
            headers={"X-Forwarded-For": "194.187.247.152"},
        )

    assert response.status_code == 403
    assert db.request.calls == []


@pytest.mark.parametrize(("method", "url"), GATED_REQUESTS)
async def test_unconfigured_secret_gives_503_even_with_a_header(
    client: AsyncClient,
    db: Database,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    url: str,
) -> None:
    monkeypatch.setattr(kaspi_proxy_config, "secret", "")

    response = await client.request(method, url, params=PAY)

    assert response.status_code == 503
    assert db.events == []


@pytest.mark.parametrize("route", ["kaspi-payment-pay", "kaspi-pay-form"])
async def test_removed_routes_are_gone(client: AsyncClient, route: str) -> None:
    response = await client.get(f"/api/kaspi/{route}")

    assert response.status_code == 404
