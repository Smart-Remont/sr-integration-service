from collections.abc import AsyncIterator, Iterator

import pytest
from httpx import ASGITransport, AsyncClient

from src.main import app
from tests.client_sign.fakes import FakeConnection, Partners, clear_overrides, install_overrides


@pytest.fixture
def connection() -> FakeConnection:
    return FakeConnection()


@pytest.fixture
def partners() -> Partners:
    return Partners()


@pytest.fixture(autouse=True)
def overrides(connection: FakeConnection, partners: Partners) -> Iterator[None]:
    install_overrides(connection, partners)
    yield
    clear_overrides()


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://service.test",
        follow_redirects=False,
    ) as test_client:
        yield test_client
