"""Fixtures of the Albato lead tests."""

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from src.database.deps import get_db_connection
from src.main import app
from tests.big_integration.fakes import FakeConnection

TOKEN = "albato-test-token"
SETTING = "public.get_setting_value_by_code"
CREATE = "crm.request_from_albato_meta"
ROUTE = "/api/albato/albato-meta-lead"


@pytest.fixture
def db() -> FakeConnection:
    return FakeConnection({SETTING: [{"result": TOKEN}], CREATE: [{"id": 123}]})


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
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
    app.dependency_overrides.clear()
