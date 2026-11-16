"""``GET /bi`` and ``GET /api/bi-badge/residents``."""

import base64
import hashlib
import re

from httpx import AsyncClient

from tests.bi_badge.conftest import PAGE_ROUTE, RESIDENT_ROWS, RESIDENTS, RESIDENTS_ROUTE
from tests.big_integration.fakes import FakeConnection


async def test_residents_come_from_the_database_and_are_cached(
    client: AsyncClient, db: FakeConnection
) -> None:
    first = await client.get(RESIDENTS_ROUTE)
    second = await client.get(RESIDENTS_ROUTE)

    assert first.status_code == 200
    assert first.json() == RESIDENT_ROWS
    assert second.json() == RESIDENT_ROWS
    assert db.calls == [(RESIDENTS, (None,))]
    assert first.headers["cache-control"] == "public, max-age=300"


async def test_page_is_one_self_contained_file(client: AsyncClient, db: FakeConnection) -> None:
    response = await client.get(PAGE_ROUTE, params={"ref": "BI0001", "utm_source": "bi_badge"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    page = response.text
    assert 'id="sr_hp_x9"' in page
    assert 'var API = "/api/bi-badge";' in page
    assert "https://smartremont.kz/confidentiality" in page
    assert "wa.me/77710155309" in page
    assert not re.search(r"<(script|link)[^>]+(src|href)=\"http", page)
    assert db.calls == []


async def test_page_allows_only_its_own_script(client: AsyncClient) -> None:
    response = await client.get(PAGE_ROUTE)

    script = re.search(r"<script>(.*?)</script>", response.text, re.DOTALL).group(1)
    digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    policy = response.headers["content-security-policy"]
    assert f"script-src 'sha256-{digest}';" in policy
    assert "connect-src 'self'" in policy
    assert "frame-ancestors 'none'" in policy
