"""Every BIG route moved from PHP answers a bad caller the way PHP did."""

import pytest
from httpx import AsyncClient

from tests.big_integration.fakes import _auth, _url


@pytest.mark.parametrize(
    ("method", "route"),
    [
        ("GET", "request-info"),
        ("GET", "request-status-info"),
        ("GET", "ddu-request-info"),
        ("GET", "ddu-flat-info"),
        ("POST", "request-constructives"),
        ("POST", "big-notify-client"),
        ("POST", "remont-preset-list-v2"),
        ("POST", "remont-preset"),
        ("GET", "remont-preset"),
    ],
)
async def test_route_requires_basic_auth(client: AsyncClient, method: str, route: str) -> None:
    for headers in ({}, _auth("wrong"), {"Authorization": "Basic not-base64"}):
        response = await client.request(method, _url(route), headers=headers)
        assert response.status_code == 401
        assert response.content == b""
        assert response.headers["www-authenticate"] == 'Basic realm="Access denied"'
        assert response.headers["content-type"] == "text/html; charset=UTF-8"
