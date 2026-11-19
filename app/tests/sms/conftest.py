"""Fixtures of the SMS cron tests."""

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from loguru import logger

from src import repository
from src.config import kcell_config, sms_cron_auth_config
from src.database.pool import get_db_pool
from src.features.sms.client import KcellClient
from src.features.sms.deps import get_kcell_client
from src.main import app
from tests.sms.fakes import (
    CRON_HEADERS,
    CRON_PASSWORD,
    CRON_USER,
    SEND_PASSWORD,
    SEND_URL,
    SEND_USER,
    STATUS_PASSWORD,
    STATUS_URL,
    STATUS_USER,
    Database,
    Kcell,
)


@pytest.fixture(autouse=True)
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sms_cron_auth_config, "username", CRON_USER)
    monkeypatch.setattr(sms_cron_auth_config, "password", CRON_PASSWORD)
    monkeypatch.setattr(kcell_config, "send_url", SEND_URL)
    monkeypatch.setattr(kcell_config, "send_username", SEND_USER)
    monkeypatch.setattr(kcell_config, "send_password", SEND_PASSWORD)
    monkeypatch.setattr(kcell_config, "status_url", STATUS_URL)
    monkeypatch.setattr(kcell_config, "status_username", STATUS_USER)
    monkeypatch.setattr(kcell_config, "status_password", STATUS_PASSWORD)


@pytest.fixture
def db() -> Database:
    return Database()


@pytest.fixture
def kcell(db: Database) -> Kcell:
    return Kcell(db)


@pytest.fixture
async def client(
    db: Database,
    kcell: Kcell,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[AsyncClient]:
    async def _pool() -> Database:
        return db

    monkeypatch.setattr(repository.base, "get_db_pool", _pool)
    app.dependency_overrides[get_db_pool] = _pool
    app.dependency_overrides[get_kcell_client] = lambda: KcellClient(
        kcell_config,
        transport=httpx.MockTransport(kcell.handle),
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers=CRON_HEADERS,
    ) as http:
        yield http
    app.dependency_overrides.clear()


@pytest.fixture
def log_lines() -> Any:
    lines: list[str] = []
    sink = logger.add(lines.append, format="{level} {message} {extra}", level="DEBUG")
    yield lines
    logger.remove(sink)
