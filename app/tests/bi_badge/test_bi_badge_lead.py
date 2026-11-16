"""``POST /api/bi-badge/lead``: the application of the BI badge form."""

import hashlib
import hmac
import json
from collections.abc import Iterator

import pytest
from asyncpg.exceptions import PostgresError
from httpx import AsyncClient
from loguru import logger

from src.config import bi_badge_config
from src.features.bi_badge.client_ip import hash_client_ip
from tests.bi_badge.conftest import CREATE, IP_SECRET, LEAD_ROUTE
from tests.big_integration.fakes import FakeConnection

DEVICE = "3f1c2a9e-5b7d-4c11-9a2e-8f6d0b4c7e21"
LEAD = {
    "name": "Айгерим",
    "phone": "+77012345678",
    "resident_id": 11,
    "area": "65,5",
    "ref": "BI0001",
    "utm_source": "bi_badge",
    "utm_medium": "qr",
    "utm_campaign": "bi_staff",
    "lang": "kz",
    "device_id": DEVICE,
    "website": "",
}


def _hash(ip: str) -> str:
    return hmac.new(IP_SECRET.encode(), ip.encode(), hashlib.sha256).hexdigest()


def _sent(db: FakeConnection) -> dict[str, object]:
    function, args = db.calls[-1]
    assert function == CREATE
    return json.loads(args[0])


def _database_error(message: str, *, hint: str | None = None, detail: str | None = None):
    fields = {"C": "P0001", "M": message}
    if hint:
        fields["H"] = hint
    if detail:
        fields["D"] = detail
    return PostgresError.new(fields)


@pytest.fixture
def log_lines() -> Iterator[list[str]]:
    lines: list[str] = []
    sink = logger.add(lambda message: lines.append(str(message)), level="DEBUG")
    yield lines
    logger.remove(sink)


async def test_lead_is_sent_to_the_database_and_answers_only_ok(
    client: AsyncClient, db: FakeConnection, log_lines: list[str]
) -> None:
    response = await client.post(LEAD_ROUTE, json=LEAD)

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert _sent(db) == {
        "name": "Айгерим",
        "phone": "+77012345678",
        "ref": "BI0001",
        "resident_id": 11,
        "area": "65,5",
        "utm_source": "bi_badge",
        "utm_medium": "qr",
        "utm_campaign": "bi_staff",
        "lang": "kz",
        "ip_hash": _hash("10.0.0.9"),
        "device_id": DEVICE,
    }
    assert not any("Айгерим" in line or "7012345678" in line for line in log_lines)


async def test_empty_values_are_left_out_and_other_complex_goes_as_text(
    client: AsyncClient, db: FakeConnection
) -> None:
    body = {"name": " Ерлан ", "phone": "87012345678", "resident_other": "ЖК Свой", "area": 70}
    body |= {"resident_id": "", "ref": "  ", "utm_term": None}

    response = await client.post(LEAD_ROUTE, json=body)

    assert response.status_code == 200
    sent = _sent(db)
    assert sent == {
        "name": "Ерлан",
        "phone": "87012345678",
        "resident_other": "ЖК Свой",
        "area": 70,
        "ip_hash": _hash("10.0.0.9"),
    }


@pytest.mark.parametrize("device_id", ["x" * 65, "<script>", "short", 12345678])
async def test_strange_device_id_is_dropped_not_refused(
    client: AsyncClient, db: FakeConnection, device_id: object
) -> None:
    response = await client.post(LEAD_ROUTE, json=LEAD | {"device_id": device_id})

    assert response.status_code == 200
    assert "device_id" not in _sent(db)


async def test_filled_trap_answers_ok_and_never_reaches_the_database(
    client: AsyncClient, db: FakeConnection
) -> None:
    response = await client.post(LEAD_ROUTE, json=LEAD | {"website": "http://spam.example"})

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert db.calls == []


async def test_validation_refusal_is_422_with_the_field(
    client: AsyncClient, db: FakeConnection, rollbacks: list[Exception]
) -> None:
    text = "Укажите номер телефона Казахстана: +7 7XX XXX XX XX"
    db.responses[CREATE] = _database_error(
        "{" + text + "}", hint="BI_BADGE_VALIDATION", detail="phone"
    )

    response = await client.post(LEAD_ROUTE, json=LEAD)

    assert response.status_code == 422
    assert response.json() == {"field": "phone", "message": text}
    assert rollbacks


async def test_validation_refusal_without_a_field_is_for_the_whole_form(
    client: AsyncClient, db: FakeConnection
) -> None:
    db.responses[CREATE] = _database_error("{Пустой запрос}", hint="BI_BADGE_VALIDATION")

    response = await client.post(LEAD_ROUTE, json=LEAD)

    assert response.status_code == 422
    assert response.json() == {"field": None, "message": "Пустой запрос"}


async def test_limit_is_429_with_its_text(
    client: AsyncClient, db: FakeConnection, rollbacks: list[Exception]
) -> None:
    db.responses[CREATE] = _database_error(
        "{Слишком много заявок. Попробуйте завтра}", hint="BI_BADGE_RATE_LIMIT"
    )

    response = await client.post(LEAD_ROUTE, json=LEAD)

    assert response.status_code == 429
    assert response.json() == {"field": None, "message": "Слишком много заявок. Попробуйте завтра"}
    assert rollbacks


@pytest.mark.parametrize(
    "error",
    [
        _database_error("{Источник BI_BADGE не найден или неактивен}"),
        PostgresError.new({"C": "22P02", "M": 'invalid input syntax: "Айгерим 77012345678"'}),
    ],
)
async def test_other_database_error_is_500_without_details_in_answer_or_log(
    client: AsyncClient, db: FakeConnection, log_lines: list[str], error: Exception
) -> None:
    db.responses[CREATE] = error

    response = await client.post(LEAD_ROUTE, json=LEAD)

    assert response.status_code == 500
    assert response.json() == {
        "field": None,
        "message": "Не удалось отправить заявку. Попробуйте ещё раз или напишите нам в WhatsApp",
    }
    joined = "\n".join(log_lines)
    assert "77012345678" not in joined
    assert "Айгерим" not in joined
    assert "BI_BADGE" not in joined


async def test_no_secret_is_503_and_nothing_is_sent(
    client: AsyncClient, db: FakeConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(bi_badge_config, "ip_secret", "")

    response = await client.post(LEAD_ROUTE, json=LEAD)

    assert response.status_code == 503
    assert response.json()["field"] is None
    assert db.calls == []


@pytest.mark.parametrize(
    ("headers", "ip"),
    [
        ({"X-Forwarded-For": "203.0.113.7, 10.0.0.1", "X-Real-IP": "198.51.100.2"}, "198.51.100.2"),
        ({"X-Real-IP": "198.51.100.2"}, "198.51.100.2"),
        ({"X-Forwarded-For": "1.2.3.4, 203.0.113.7"}, "203.0.113.7"),
        ({"X-Forwarded-For": "203.0.113.7, garbage", "X-Real-IP": "bad"}, "10.0.0.9"),
        ({"X-Forwarded-For": " 2001:db8::1 "}, "2001:db8::1"),
        ({}, "10.0.0.9"),
    ],
)
async def test_client_ip_comes_from_proxy_headers_and_only_its_hash_is_sent(
    client: AsyncClient, db: FakeConnection, headers: dict[str, str], ip: str
) -> None:
    response = await client.post(LEAD_ROUTE, json=LEAD, headers=headers)

    assert response.status_code == 200
    sent = _sent(db)
    assert sent["ip_hash"] == _hash(ip)
    assert ip not in json.dumps(sent)


def test_ip_hash_is_hmac_sha256_with_the_secret() -> None:
    assert (
        hash_client_ip("203.0.113.7", "s")
        == hmac.new(b"s", b"203.0.113.7", hashlib.sha256).hexdigest()
    )
    assert hash_client_ip("203.0.113.7", "s") != hash_client_ip("203.0.113.7", "t")
    assert len(hash_client_ip(None, "s")) == 64
