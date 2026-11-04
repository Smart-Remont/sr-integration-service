"""Fixtures of the mailing cron tests."""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from loguru import logger

from src import repository
from src.config import mailing_config, mailing_cron_auth_config
from src.database.pool import get_db_pool
from src.features.mailing.deps import get_smtp_transport_class
from src.main import app
from tests.mailing.fakes import (
    CC_ADDRESS,
    CRON_HEADERS,
    CRON_PASSWORD,
    CRON_USER,
    FROM_ADDRESS,
    LINK_BASE,
    Database,
    MailServer,
)


@pytest.fixture(autouse=True)
def configured(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(mailing_cron_auth_config, "username", CRON_USER)
    monkeypatch.setattr(mailing_cron_auth_config, "password", CRON_PASSWORD)
    values = {
        "smtp_host": "smtp.test.invalid",
        "smtp_port": 587,
        "smtp_username": FROM_ADDRESS,
        "smtp_password": "smtp-pass",
        "from_name": "SmartRemont",
        "link_base_url": LINK_BASE,
        "expeditor_cc_address": CC_ADDRESS,
        "expeditor_cc_name": "Копия",
        "office_public_dir": str(tmp_path),
    }
    for name, value in values.items():
        monkeypatch.setattr(mailing_config, name, value, raising=False)


@pytest.fixture
def db() -> Database:
    return Database()


@pytest.fixture
def mail(db: Database) -> MailServer:
    return MailServer(db)


@pytest.fixture
async def client(
    db: Database,
    mail: MailServer,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[AsyncClient]:
    async def _pool() -> Database:
        return db

    monkeypatch.setattr(repository.base, "get_db_pool", _pool)
    app.dependency_overrides[get_db_pool] = _pool
    app.dependency_overrides[get_smtp_transport_class] = mail.transport_class
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
