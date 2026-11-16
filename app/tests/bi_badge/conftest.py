"""Fixtures of the BI badge form tests. The database is a script of stored-function answers."""

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from src.config import bi_badge_config
from src.database.deps import get_db_connection
from src.features.bi_badge.service import resident_cache
from src.main import app
from tests.big_integration.fakes import FakeConnection

IP_SECRET = "bi-badge-test-secret"
CREATE = "crm.request_from_bi_badge"
RESIDENTS = "crm.bi_badge_resident_read"
LEAD_ROUTE = "/api/bi-badge/lead"
RESIDENTS_ROUTE = "/api/bi-badge/residents"
PAGE_ROUTE = "/bi"
RESIDENT_ROWS = [
    {"resident_id": 11, "resident_name": "Arnau", "city_id": 1, "city_name": "Астана"},
    {"resident_id": 12, "resident_name": "Nexpo", "city_id": 2, "city_name": "Алматы"},
]


@pytest.fixture(autouse=True)
def ip_secret(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(bi_badge_config, "ip_secret", IP_SECRET)
    return IP_SECRET


@pytest.fixture(autouse=True)
def empty_resident_cache() -> None:
    resident_cache.clear()


@pytest.fixture
def db() -> FakeConnection:
    return FakeConnection({CREATE: [{"request_from_bi_badge": 501}], RESIDENTS: RESIDENT_ROWS})


@pytest.fixture
def rollbacks() -> list[Exception]:
    return []


@pytest.fixture
async def client(db: FakeConnection, rollbacks: list[Exception]) -> AsyncIterator[AsyncClient]:
    async def _connection() -> AsyncIterator[FakeConnection]:
        try:
            yield db
        except Exception as exc:
            rollbacks.append(exc)
            raise

    app.dependency_overrides[get_db_connection] = _connection
    transport = ASGITransport(app=app, client=("10.0.0.9", 50000))
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
    app.dependency_overrides.clear()
