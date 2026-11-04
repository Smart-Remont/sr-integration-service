"""``get-constructives``, moved from PHP."""

import pytest
from asyncpg.exceptions import InvalidTextRepresentationError, RaiseError
from httpx import AsyncClient

from tests.big_integration.fakes import FakeConnection, _auth, _url

FUNCTION = "rest.bi_get_client_request_material_json"
ROUTE = _url("get-constructives")


async def test_value_of_the_function_is_decoded_and_pretty_printed(
    client: AsyncClient, db: FakeConnection
) -> None:
    value = '{"application_id":7,"material_list":[{"material_name":"А/Б"}]}'
    db.responses = {FUNCTION: [{"id": value}]}

    response = await client.post(ROUTE, json={"application_id": 7}, headers=_auth())

    assert response.status_code == 200
    assert response.json() == {
        "data": {"application_id": 7, "material_list": [{"material_name": "А/Б"}]},
        "response": True,
        "error": {"code": 0, "message": ""},
    }
    assert b'"material_name": "\xd0\x90/\xd0\x91"' in response.content
    assert db.calls == [(FUNCTION, ("7",))]


@pytest.mark.parametrize(
    ("body", "bound"),
    [
        (b'{"application_id":7}', "7"),
        (b'{"application_id":"7"}', "7"),
        (b'{"application_id":" 7 "}', " 7 "),
        (b'{"application_id":1.5}', "1.5"),
        (b'{"application_id":1e25}', "1.0E+25"),
        (b'{"application_id":true}', "1"),
        (b'{"application_id":false}', ""),
        (b'{"application_id":null}', None),
        (b'{"application_id":[1]}', "Array"),
        (b'{"application_id":"7\\u0000x"}', "7"),
        (b'{"other":7}', None),
        (b"[7]", None),
        (b"{", None),
        (b"", None),
        (b"null", None),
        (b"5", None),
        (b"true", None),
        (b'"58"', "5"),
        (b'""', ""),
    ],
)
async def test_application_id_is_bound_as_pdo_did(
    client: AsyncClient, db: FakeConnection, body: bytes, bound: str | None
) -> None:
    db.responses = {FUNCTION: [{"id": "{}"}]}

    await client.post(ROUTE, content=body, headers=_auth())

    assert db.calls == [(FUNCTION, (bound,))]


async def test_string_body_with_a_multibyte_first_letter_fails_as_invalid_utf8(
    client: AsyncClient, db: FakeConnection
) -> None:
    db.responses = {FUNCTION: [{"id": "{}"}]}

    response = await client.post(ROUTE, content='"я"'.encode(), headers=_auth())

    assert response.status_code == 400
    assert "invalid byte sequence" in response.json()["error"]["message"]
    assert db.calls == []


async def test_function_error_is_400_with_a_code(client: AsyncClient, db: FakeConnection) -> None:
    db.responses = {
        FUNCTION: InvalidTextRepresentationError('invalid input syntax for type integer: "x"'),
    }

    response = await client.post(ROUTE, json={"application_id": "x"}, headers=_auth())

    assert response.status_code == 400
    payload = response.json()
    assert payload["data"] == "P400"
    assert payload["error"]["code"] == 400
    assert payload["error"]["message"].startswith("Произошла необработанная ошибка в БД SQLSTATE")


async def test_raised_code_is_kept(client: AsyncClient, db: FakeConnection) -> None:
    db.responses = {FUNCTION: RaiseError("{P002 Текст}")}

    response = await client.post(ROUTE, json={"application_id": 1}, headers=_auth())

    assert response.json()["data"] == "P002"


async def test_null_value_is_null_data(client: AsyncClient, db: FakeConnection) -> None:
    db.responses = {FUNCTION: [{"id": None}]}

    response = await client.post(ROUTE, json={"application_id": 1}, headers=_auth())

    assert response.json()["data"] is None


@pytest.mark.parametrize(
    ("value", "shown"),
    [("{}", {}), ('{"0":"a"}', {"0": "a"}), ("[]", [])],
)
async def test_value_keeps_objects_as_objects(
    client: AsyncClient, db: FakeConnection, value: str, shown: object
) -> None:
    db.responses = {FUNCTION: [{"id": value}]}

    response = await client.post(ROUTE, json={"application_id": 1}, headers=_auth())

    assert response.json()["data"] == shown


async def test_other_method_and_wrong_caller(client: AsyncClient, db: FakeConnection) -> None:
    other = await client.get(ROUTE, headers=_auth())
    anonymous = await client.post(ROUTE, json={"application_id": 1})

    assert other.status_code == 400
    assert other.json()["data"] == {}
    assert anonymous.status_code == 401
    assert db.calls == []
