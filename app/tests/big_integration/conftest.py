"""Fixtures of the BIG tests. ``test_contract.py`` has its own, which win over these in that file."""

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from src.database.deps import get_db_connection
from src.main import app
from tests.big_integration.fakes import FakeConnection


@pytest.fixture
def db() -> FakeConnection:
    return FakeConnection({})


@pytest.fixture
async def client(db: FakeConnection) -> AsyncIterator[AsyncClient]:
    async def _connection() -> AsyncIterator[FakeConnection]:
        yield db

    app.dependency_overrides[get_db_connection] = _connection
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
    app.dependency_overrides.clear()
