"""``big-notify-client``, moved from PHP. This route alone has its own error envelope."""

from collections.abc import AsyncIterator

import pytest
from asyncpg.exceptions import RaiseError
from httpx import AsyncClient

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
