"""Fixtures of the 1C exchange tests."""

import base64
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from src.config import buh_1c_auth_config
from src.database.pool import get_db_pool
from src.main import app
from tests.big_integration.fakes import FakeConnection

USER = "buh-user"
PASSWORD = "buh-password"
ROUTE = "/api/buh_1c/buh"
CHECK = "rest.get_buh_url"
JOURNAL = "public.insert_1c_json"
LOT_READ = "rest.buh_lot_read"
LOT_UPD = "rest.buh_lot_upd"
MATERIAL_UPD = "rest.buh_material_upd"
WAREHOUSE_UPD = "rest.buh_warehouse_upd"
LOGIN = {"Authorization": "Basic " + base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()}


class _Transaction:
    """Records how a step ended, so a test can tell what would have been committed."""

    def __init__(self, connection: Buh1cConnection) -> None:
        self.connection = connection

    async def __aenter__(self) -> None:
        self.connection.transactions_opened += 1

    async def __aexit__(self, kind: type[BaseException] | None, *_: object) -> None:
        self.connection.transactions_closed.append("rollback" if kind else "commit")


class Buh1cConnection(FakeConnection):
    def __init__(self, responses: dict[str, object]) -> None:
        super().__init__(responses)
        self.transactions_opened = 0
        self.transactions_closed: list[str] = []
        self.config_queries: list[str] = []

    def transaction(self) -> _Transaction:
        return _Transaction(self)

    async def fetchval(self, query: str, *args: object) -> None:
        self.config_queries.append(query)
        await super().fetchval(query, *args)

    def functions(self) -> list[str]:
        return [function for function, _ in self.calls]


class FakePool:
    def __init__(self, connection: Buh1cConnection) -> None:
        self.connection = connection
        self.acquired = 0

    def acquire(self) -> FakePool:
        return self

    async def __aenter__(self) -> Buh1cConnection:
        self.acquired += 1
        return self.connection

    async def __aexit__(self, *_: object) -> None:
        return None


@pytest.fixture(autouse=True)
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(buh_1c_auth_config, "username", USER)
    monkeypatch.setattr(buh_1c_auth_config, "password", PASSWORD)


@pytest.fixture
def db() -> Buh1cConnection:
    return Buh1cConnection(
        {
            CHECK: [{"get_buh_url": "http://1c.test/x"}],
            JOURNAL: [{"id": 0}],
            LOT_READ: [{"id": '{"value": [], "status": true}'}],
            LOT_UPD: [{"id": '{"lot": []}'}],
            MATERIAL_UPD: [{"id": '{"status": "OK", "error": ""}'}],
            WAREHOUSE_UPD: [{"id": '{"warehouse": []}'}],
        }
    )


@pytest.fixture
def pool(db: Buh1cConnection) -> FakePool:
    return FakePool(db)


@pytest.fixture
async def client(pool: FakePool) -> AsyncIterator[AsyncClient]:
    async def _pool() -> FakePool:
        return pool

    app.dependency_overrides[get_db_pool] = _pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", headers=LOGIN) as http:
        yield http
    app.dependency_overrides.clear()


@pytest.fixture
async def anonymous(pool: FakePool) -> AsyncIterator[AsyncClient]:
    async def _pool() -> FakePool:
        return pool

    app.dependency_overrides[get_db_pool] = _pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
    app.dependency_overrides.clear()
