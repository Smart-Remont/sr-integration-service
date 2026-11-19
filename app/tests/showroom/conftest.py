"""Fixtures of the showroom cron tests."""

import base64
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from src.config import showroom_config, showroom_cron_auth_config
from src.database.pool import get_db_pool
from src.features.showroom.client import ShowroomClient
from src.features.showroom.deps import get_showroom_client
from src.main import app
from tests.big_integration.fakes import FakeConnection

CRON_USER = "cron"
CRON_PASSWORD = "cron-password"
BI_URL = "https://bi.test/wbs/api/applications/showroomsmartremont/"
FUNCTION = "rest.read_marketing_remont_list"
ROUTE = "/api/showroom/showroom-info-list"
CRON_HEADERS = {
    "Authorization": "Basic " + base64.b64encode(f"{CRON_USER}:{CRON_PASSWORD}".encode()).decode(),
}


class _Transaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_: object) -> None:
        return None


class ShowroomConnection(FakeConnection):
    def transaction(self) -> _Transaction:
        return _Transaction()


class FakePool:
    """Counts how many connections are out while BI Group answers."""

    def __init__(self, connection: ShowroomConnection) -> None:
        self.connection = connection
        self.out = 0

    def acquire(self) -> FakePool:
        return self

    async def __aenter__(self) -> ShowroomConnection:
        self.out += 1
        return self.connection

    async def __aexit__(self, *_: object) -> None:
        self.out -= 1


@dataclass
class BiGroup:
    pool: FakePool
    status: int = 200
    body: bytes = b'{"ok":true}'
    error: Exception | None = None
    requests: list[httpx.Request] = field(default_factory=list)
    connections_out: list[int] = field(default_factory=list)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        self.connections_out.append(self.pool.out)
        if self.error:
            raise self.error
        return httpx.Response(self.status, content=self.body)


@pytest.fixture(autouse=True)
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(showroom_cron_auth_config, "username", CRON_USER)
    monkeypatch.setattr(showroom_cron_auth_config, "password", CRON_PASSWORD)
    monkeypatch.setattr(showroom_config, "bi_url", BI_URL)


@pytest.fixture
def db() -> ShowroomConnection:
    return ShowroomConnection({FUNCTION: [{"result": '{"SMART_REMONT_SHOW_ROOMS":[]}'}]})


@pytest.fixture
def pool(db: ShowroomConnection) -> FakePool:
    return FakePool(db)


@pytest.fixture
def bi_group(pool: FakePool) -> BiGroup:
    return BiGroup(pool=pool)


@pytest.fixture
async def client(pool: FakePool, bi_group: BiGroup) -> AsyncIterator[AsyncClient]:
    def _client() -> ShowroomClient:
        return ShowroomClient(BI_URL, transport=httpx.MockTransport(bi_group.handle))

    async def _pool() -> FakePool:
        return pool

    app.dependency_overrides[get_db_pool] = _pool
    app.dependency_overrides[get_showroom_client] = _client
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers=CRON_HEADERS,
    ) as http:
        yield http
    app.dependency_overrides.clear()
