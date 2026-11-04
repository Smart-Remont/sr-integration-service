"""``big-notify-client``, moved from PHP. This route alone has its own error envelope."""

from collections.abc import AsyncIterator

import pytest
from asyncpg.exceptions import RaiseError
from httpx import ASGITransport, AsyncClient

from src.database.deps import get_db_connection
from src.main import app
from tests.big_integration.fakes import FakeConnection, _auth, _url


async def test_notify_passes_raw_body_as_text(client: AsyncClient, db: FakeConnection) -> None:
    db.responses = {"rest.big_notify_client": [{"big_notify_client": "ok"}]}
    body = '{"phone":"7700", "text":"Здравствуйте"}'

    response = await client.post(
        _url("big-notify-client"),
        content=body.encode(),
        headers=_auth(),
    )

    assert response.status_code == 200
    assert response.json() == {
        "data": "ok",
        "response": True,
        "error": {"code": 0, "message": ""},
    }
    assert db.calls == [("rest.big_notify_client", (body,))]


@pytest.fixture
async def rollback_tracker(db: FakeConnection) -> AsyncIterator[list[Exception]]:
    errors: list[Exception] = []

    async def connection() -> AsyncIterator[FakeConnection]:
        try:
            yield db
        except Exception as exc:
            errors.append(exc)
            raise

    app.dependency_overrides[get_db_connection] = connection
    yield errors


async def test_notify_error_rolls_back_and_keeps_php_envelope(
    client: AsyncClient,
    db: FakeConnection,
    rollback_tracker: list[Exception],
) -> None:
    db.responses = {"rest.big_notify_client": RaiseError("{Неверный телефон}")}

    response = await client.post(_url("big-notify-client"), content=b"{}", headers=_auth())

    assert response.status_code == 400
    assert response.json() == {
        "data": {},
        "response": False,
        "error": {"code": 400, "message": "Неверный телефон"},
    }
    assert len(rollback_tracker) == 1


async def test_notify_response_is_pretty_printed_like_php(
    client: AsyncClient, db: FakeConnection
) -> None:
    db.responses = {"rest.big_notify_client": [{"big_notify_client": "OK/1"}]}

    response = await client.post(_url("big-notify-client"), content=b"{}", headers=_auth())

    assert response.content == (
        b'{\n    "data": "OK/1",\n    "response": true,\n'
        b'    "error": {\n        "code": 0,\n        "message": ""\n    }\n}'
    )


async def test_notify_rejects_a_body_that_is_not_utf8(
    client: AsyncClient, db: FakeConnection
) -> None:
    response = await client.post(_url("big-notify-client"), content=b"\xff", headers=_auth())

    assert response.status_code == 400
    assert "invalid byte sequence" in response.json()["error"]["message"]
    assert db.calls == []


async def test_notify_rejects_other_methods_with_its_own_envelope(client: AsyncClient) -> None:
    response = await client.get(_url("big-notify-client"), headers=_auth())

    assert response.status_code == 400
    assert response.json()["error"] == {"code": 400, "message": "Неподдерживаемый метод"}


@pytest.mark.parametrize("body", [b"", b"   ", b" \t\r\n", b"\x00", b"\x0b "])
async def test_blank_body_reaches_the_function_as_empty_text(
    client: AsyncClient, db: FakeConnection, body: bytes
) -> None:
    # Zend getRawBody gives false for a blank body and PDO sends that as an empty string.
    db.responses = {"rest.big_notify_client": lambda args: [{"value": "OK"}]}

    response = await client.post(_url("big-notify-client"), content=body, headers=_auth())

    assert response.status_code == 200
    assert db.calls == [("rest.big_notify_client", ("",))]


@pytest.mark.parametrize(
    ("body", "shown"),
    [
        (b'{"a":"\xc3"}', "0xc3 0x22"),
        (b"\xe2\x28\xa1", "0xe2 0x28 0xa1"),
        (b"\xff", "0xff"),
        (b"\xc3", "0xc3"),
    ],
)
async def test_invalid_utf8_shows_the_bytes_postgresql_shows(
    client: AsyncClient, db: FakeConnection, body: bytes, shown: str
) -> None:
    response = await client.post(_url("big-notify-client"), content=body, headers=_auth())

    assert response.status_code == 400
    message = response.json()["error"]["message"]
    assert message.startswith("Произошла необработанная ошибка в БД SQLSTATE[22021]")
    assert message.endswith(f'invalid byte sequence for encoding "UTF8": {shown}')
    assert db.calls == []


async def test_notify_runs_on_custom_plans_as_in_a_new_php_session(
    client: AsyncClient, db: FakeConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    # On a generic plan a too long name fails after the sequence moved, on a custom one before it.
    queries: list[str] = []

    async def record(query: str, *args: object) -> None:
        queries.append(query)

    monkeypatch.setattr(db, "fetchval", record)
    db.responses = {"rest.big_notify_client": lambda args: [{"value": "OK"}]}

    response = await client.post(_url("big-notify-client"), content=b"{}", headers=_auth())

    assert response.status_code == 200
    assert "select set_config('plan_cache_mode', 'force_custom_plan', true)" in queries


async def test_failed_commit_is_not_answered_as_done(db: FakeConnection) -> None:
    db.responses = {"rest.big_notify_client": [{"big_notify_client": "ok"}]}

    async def connection() -> AsyncIterator[FakeConnection]:
        yield db
        raise ConnectionError("commit failed")

    app.dependency_overrides[get_db_connection] = connection
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as http:
            response = await http.post(_url("big-notify-client"), content=b"{}", headers=_auth())
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 500
    assert b'"response": true' not in response.content
    assert b'"response":true' not in response.content
