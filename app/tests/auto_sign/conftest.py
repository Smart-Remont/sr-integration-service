"""Fixtures of the auto-sign cron tests."""

from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from loguru import logger

from src import repository
from src.config import (
    app_config,
    auto_sign_config,
    auto_sign_cron_auth_config,
    minio_config,
)
from src.database.pool import get_db_pool
from src.features.auto_sign import document_upload, operator_signing
from src.features.auto_sign.deps import (
    get_auto_sign_mynca,
    get_partner_transport,
    get_signing_failure_brake,
)
from src.features.auto_sign.failure_brake import SigningFailureBrake
from src.main import app
from src.shared import cron_run
from src.shared.mynca_client import MyncaClient
from tests.auto_sign.fakes import (
    CRON_HEADERS,
    CRON_PASSWORD,
    CRON_USER,
    MASTER_KEY,
    MYNCA_TOKEN,
    MYNCA_URL,
    PARTNER_URL,
    Database,
    Mynca,
)


@pytest.fixture(autouse=True)
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_config, "env", "prod")
    monkeypatch.setattr(auto_sign_cron_auth_config, "username", CRON_USER)
    monkeypatch.setattr(auto_sign_cron_auth_config, "password", CRON_PASSWORD)
    monkeypatch.setattr(auto_sign_config, "mynca_base_url", MYNCA_URL)
    monkeypatch.setattr(auto_sign_config, "mynca_token", MYNCA_TOKEN)
    monkeypatch.setattr(auto_sign_config, "master_key", MASTER_KEY)
    monkeypatch.setattr(auto_sign_config, "partner_url", PARTNER_URL)
    monkeypatch.setattr(minio_config, "endpoint", "https://minio.test")
    monkeypatch.setattr(minio_config, "access_key", "minio-access")
    monkeypatch.setattr(minio_config, "secret_key", "minio-secret")


@pytest.fixture
def db() -> Database:
    return Database()


@pytest.fixture
def mynca(db: Database) -> Mynca:
    return Mynca(db)


class Partner:
    """The partner backend of ``pdf-find-sum``."""

    def __init__(self) -> None:
        self.urls: list[str] = []
        self.answer: httpx.Response | Exception = httpx.Response(200, content=b"not a pdf")

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.urls.append(str(request.url))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


@pytest.fixture
def brake() -> SigningFailureBrake:
    return SigningFailureBrake()


@pytest.fixture
def partner() -> Partner:
    return Partner()


class Storage:
    def __init__(self) -> None:
        self.puts: list[dict[str, Any]] = []
        self.error: Exception | None = None

    async def put_object(self, *, key: str, body: bytes, content_type: str) -> None:
        if self.error is not None:
            raise self.error
        self.puts.append({"key": key, "body": body, "content_type": content_type})


@pytest.fixture
def storage(monkeypatch: pytest.MonkeyPatch) -> Storage:
    fake = Storage()
    monkeypatch.setattr(document_upload, "put_object", fake.put_object)
    return fake


@pytest.fixture
async def client(
    db: Database,
    mynca: Mynca,
    partner: Partner,
    brake: SigningFailureBrake,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[AsyncClient]:
    async def _pool() -> Database:
        return db

    monkeypatch.setattr(repository.base, "get_db_pool", _pool)
    app.dependency_overrides[get_db_pool] = _pool
    app.dependency_overrides[get_auto_sign_mynca] = lambda: MyncaClient(
        MYNCA_URL,
        MYNCA_TOKEN,
        transport=httpx.MockTransport(mynca.handle),
    )
    app.dependency_overrides[get_signing_failure_brake] = lambda: brake
    app.dependency_overrides[get_partner_transport] = lambda: httpx.MockTransport(partner.handle)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers=CRON_HEADERS,
    ) as http:
        yield http
    app.dependency_overrides.clear()


@pytest.fixture
def log_lines() -> Iterator[list[str]]:
    lines: list[str] = []
    sink = logger.add(lines.append, format="{level} {message} {extra} {exception}", level="DEBUG")
    yield lines
    logger.remove(sink)


@pytest.fixture
def short_read_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(operator_signing, "READ_BACK_SECONDS", 0.05)
    monkeypatch.setattr(operator_signing, "READ_BACK_PAUSE_SECONDS", 0.01)


@pytest.fixture
def short_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cron_run, "DB_STEP_SECONDS", 0.01)
    monkeypatch.setattr(cron_run, "ROLLBACK_MARGIN_SECONDS", 0.01)
