"""``bigapp-form`` and ``big-crm-form``, moved from PHP."""

from collections.abc import AsyncIterator

import pytest
from asyncpg.exceptions import DivisionByZeroError, RaiseError
from httpx import AsyncClient

from src.database.deps import get_db_connection
from src.main import app
from tests.big_integration.fakes import FakeConnection, _auth, _url

ROUTES = [
    ("bigapp-form", "crm.request_from_bigapp"),
    ("big-crm-form", "crm.request_from_bigcrm"),
]


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


@pytest.mark.parametrize(("route", "function"), ROUTES)
async def test_lead_goes_filtered_and_pretty_and_answers_the_value(
    client: AsyncClient, db: FakeConnection, route: str, function: str
) -> None:
    db.responses = {function: [{"id": 0}]}

    response = await client.post(
        _url(route),
        content='{"client_fio":" <b>А/Б</b> 😀","n":5,"k":{"x":null},"e":{}}'.encode(),
        headers=_auth(),
    )

    assert response.status_code == 200
    assert response.content == (
        b'{\n    "data": 0,\n    "response": true,\n    "error": {\n'
        b'        "code": 0,\n        "message": ""\n    }\n}'
    )
    assert response.headers["content-type"] == "application/json"
    assert response.headers["cache-control"] == "no-cache, must-revalidate, max-age=0"
    assert db.calls == [
        (
            function,
            (
                '{\n    "client_fio": "&lt;b&gt;А/Б&lt;/b&gt;",\n    "n": "5",\n    "k": {\n'
                '        "x": ""\n    },\n    "e": []\n}',
            ),
        ),
    ]


@pytest.mark.parametrize(
    ("body", "text"),
    [
        (b"", "null"),
        (b"{", "null"),
        (b"null", "null"),
        (b"5", "5"),
        (b"true", "true"),
        (b'"a/b<"', '"a/b<"'),
        (b"1e999", ""),
    ],
)
async def test_body_that_is_not_an_array_reaches_the_function_unfiltered(
    client: AsyncClient, db: FakeConnection, body: bytes, text: str
) -> None:
    db.responses = {"crm.request_from_bigapp": [{"id": 0}]}

    await client.post(_url("bigapp-form"), content=body, headers=_auth())

    assert db.calls == [("crm.request_from_bigapp", (text,))]


async def test_multipart_has_no_raw_body_as_in_php(client: AsyncClient, db: FakeConnection) -> None:
    db.responses = {"crm.request_from_bigapp": [{"id": 0}]}

    await client.post(
        _url("bigapp-form"),
        content=b'{"client_fio":"x"}',
        headers={**_auth(), "Content-Type": "multipart/form-data; boundary=b"},
    )

    assert db.calls == [("crm.request_from_bigapp", ("null",))]


@pytest.mark.parametrize(("route", "function"), ROUTES)
@pytest.mark.parametrize(
    ("raised", "code", "message"),
    [
        ("{ФИО не может быть пустым}", "P400", "ФИО не может быть пустым"),
        ("{P001 Текст}", "P001", "P001 Текст"),
        ("{P003 Текст}", "P400", "P003 Текст"),
        ("{P014}", "P014", "P014"),
        ("{P015}", "P400", "P015"),
    ],
)
async def test_refusal_is_400_with_the_code_and_rolls_back(
    client: AsyncClient,
    db: FakeConnection,
    rollback_tracker: list[Exception],
    route: str,
    function: str,
    raised: str,
    code: str,
    message: str,
) -> None:
    db.responses = {function: RaiseError(raised)}

    response = await client.post(_url(route), content=b"{}", headers=_auth())

    assert response.status_code == 400
    assert response.json() == {
        "data": code,
        "response": False,
        "error": {"code": 400, "message": message},
    }
    assert len(rollback_tracker) == 1


async def test_failure_text_is_answered_and_not_logged(
    client: AsyncClient, db: FakeConnection, caplog: pytest.LogCaptureFixture
) -> None:
    db.responses = {"crm.request_from_bigapp": DivisionByZeroError("тайна")}

    response = await client.post(_url("bigapp-form"), content=b"{}", headers=_auth())

    assert response.status_code == 400
    assert response.json()["data"] == "P400"
    assert "тайна" in response.json()["error"]["message"]
    assert "тайна" not in caplog.text


@pytest.mark.parametrize(("route", "function"), ROUTES)
@pytest.mark.parametrize("method", ["GET", "PUT", "DELETE", "PATCH"])
async def test_other_method_is_400_and_calls_nothing(
    client: AsyncClient, db: FakeConnection, route: str, function: str, method: str
) -> None:
    response = await client.request(method, _url(route), headers=_auth())

    assert response.status_code == 400
    assert response.json() == {
        "data": {},
        "response": False,
        "error": {"code": 400, "message": "Неподдерживаемый метод"},
    }
    assert db.calls == []
    assert function


@pytest.mark.parametrize(("route", "function"), ROUTES)
@pytest.mark.parametrize("headers", [{}, {"Authorization": "Basic d3Jvbmc6d3Jvbmc="}])
async def test_wrong_caller_is_401_without_body(
    client: AsyncClient,
    db: FakeConnection,
    route: str,
    function: str,
    headers: dict[str, str],
) -> None:
    response = await client.post(_url(route), content=b"{}", headers=headers)

    assert response.status_code == 401
    assert response.content == b""
    assert response.headers["www-authenticate"] == 'Basic realm="Access denied"'
    assert db.calls == []
    assert function


async def test_trailing_address_parts_work_as_in_zend(
    client: AsyncClient, db: FakeConnection
) -> None:
    db.responses = {"crm.request_from_bigcrm": [{"id": 0}]}

    response = await client.post(_url("big-crm-form/x/1"), content=b"{}", headers=_auth())

    assert response.status_code == 200
