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


async def test_lowercase_basic_scheme_is_refused_as_in_php(client: AsyncClient) -> None:
    # Production PHP 7.4 sets PHP_AUTH_USER only for the exact "Basic " prefix.
    good = _auth()["Authorization"]
    headers = {"Authorization": good.replace("Basic ", "basic ", 1)}

    response = await client.get(_url("request-info"), headers=headers)

    assert response.status_code == 401
    assert response.content == b""
