"""Fixtures of the ``kaspi-request-pay`` tests."""

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from src import repository
from src.config import kaspi_proxy_config
from src.database.deps import get_db_connection
from src.main import app
from tests.kaspi.fakes import PROXY_HEADERS, PROXY_SECRET, Database, KaspiConnection


@pytest.fixture(autouse=True)
def proxy_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kaspi_proxy_config, "secret", PROXY_SECRET)


@pytest.fixture
def db() -> Database:
    return Database()


@pytest.fixture
async def client(db: Database, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    async def _connection() -> AsyncIterator[KaspiConnection]:
        try:
            yield db.request
        except Exception:
            db.events.append("rollback")
            raise
        db.committed.extend(db.request.pending)
        db.events.append("commit")

    async def _pool() -> Database:
        return db

    monkeypatch.setattr(repository.base, "get_db_pool", _pool)
    app.dependency_overrides[get_db_connection] = _connection
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers=PROXY_HEADERS,
    ) as http:
        yield http
    app.dependency_overrides.clear()
