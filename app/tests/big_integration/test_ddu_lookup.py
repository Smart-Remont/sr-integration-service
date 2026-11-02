"""Short lookups moved from PHP. Expected bodies were checked against the PHP dev stand.

The fake connection answers stored-function calls, so the whole stack runs, including the
exception handlers that must both roll the request back and keep the PHP envelope.
"""

from datetime import datetime
from decimal import Decimal

import pytest
from asyncpg.exceptions import RaiseError, UndefinedFunctionError
from httpx import AsyncClient

from tests.big_integration.fakes import FakeConnection, _auth, _error, _ok, _url


@pytest.mark.parametrize(
    ("route", "function"),
    [
        ("request-info", "rest.ddu_request_info"),
        ("request-status-info", "rest.ddu_request_and_status_info"),
    ],
)
async def test_request_lookup_passes_empty_keys_as_null(
    client: AsyncClient, db: FakeConnection, route: str, function: str
) -> None:
    db.responses = {function: [{"client_request_id": 7, "price": Decimal("10.50")}]}

    response = await client.get(
        _url(route),
        params={"deal_id": "0", "order_id": "318796"},
        headers=_auth(),
    )

    assert response.status_code == 200
    assert response.json() == _ok({"client_request_id": 7, "price": "10.50"})
    assert db.calls == [(function, (None, None, "318796"))]


async def test_value_with_nul_byte_ends_at_it_as_in_libpq(
    client: AsyncClient, db: FakeConnection
) -> None:
    db.responses = {"rest.ddu_request_info": []}

    response = await client.get(
        _url("request-info"), params={"order_id": "ab\0cd"}, headers=_auth()
    )

    assert response.status_code == 200
    assert db.calls == [("rest.ddu_request_info", (None, None, "ab"))]


async def test_client_request_id_with_nul_byte_ends_at_it_as_in_libpq(
    client: AsyncClient, db: FakeConnection
) -> None:
    db.responses = {"rest.ddu_request_info_by_client_request": []}

    response = await client.get(
        _url("ddu-request-info"), params={"client_request_id": "42\0x"}, headers=_auth()
    )

    assert response.status_code == 200
    assert db.calls == [("rest.ddu_request_info_by_client_request", (42,))]


async def test_functions_run_on_custom_plans_as_in_a_new_php_session(
    client: AsyncClient, db: FakeConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    # On a generic plan rest.ddu_request_info returned the other request of a deal with two.
    queries: list[str] = []

    async def record(query: str, *args: object) -> None:
        queries.append(query)

    monkeypatch.setattr(db, "fetchval", record)
    db.responses = {"rest.ddu_request_info": []}

    response = await client.get(_url("request-info"), params={"deal_id": "1"}, headers=_auth())

    assert response.status_code == 200
    assert queries.count("select set_config('plan_cache_mode', 'force_custom_plan', true)") == 1


async def test_lookup_without_row_returns_null_data(
    client: AsyncClient, db: FakeConnection
) -> None:
    db.responses = {"rest.ddu_flat_info": []}

    response = await client.get(_url("ddu-flat-info"), headers=_auth())

    assert response.status_code == 200
    assert response.json() == _ok(None)
    assert db.calls == [("rest.ddu_flat_info", (None,))]


async def test_lookup_reads_post_form_like_php(client: AsyncClient, db: FakeConnection) -> None:
    db.responses = {
        "rest.ddu_request_info_by_client_request": [
            {"request_date": datetime(2026, 9, 9, 9, 1, 44)},
        ],
    }

    response = await client.post(
        _url("ddu-request-info"),
        data={"client_request_id": "3219655"},
        headers=_auth(),
    )

    assert response.json() == _ok({"request_date": "2026-09-09 09:01:44"})
    assert db.calls == [("rest.ddu_request_info_by_client_request", (3219655,))]


async def test_query_string_wins_over_form(client: AsyncClient, db: FakeConnection) -> None:
    db.responses = {"rest.ddu_flat_info": []}

    await client.post(
        _url("ddu-flat-info"),
        params={"flat_guid": "from-query"},
        data={"flat_guid": "from-form"},
        headers=_auth(),
    )

    assert db.calls == [("rest.ddu_flat_info", ("from-query",))]


@pytest.mark.parametrize(
    ("path", "params"),
    [
        ("ddu-flat-info/", {"flat_guid": "abc"}),
        ("ddu-flat-info/flat_guid/abc", {}),
        ("ddu-flat-info/flat_guid/abc/", {"flat_guid": "query-loses"}),
    ],
)
async def test_address_forms_of_the_zend_router(
    client: AsyncClient, db: FakeConnection, path: str, params: dict[str, str]
) -> None:
    db.responses = {"rest.ddu_flat_info": []}

    response = await client.get(_url(path), params=params, headers=_auth())

    assert response.status_code == 200
    assert db.calls == [("rest.ddu_flat_info", ("abc",))]


@pytest.mark.parametrize("method", ["PUT", "DELETE"])
async def test_lookup_does_not_check_the_method(
    client: AsyncClient, db: FakeConnection, method: str
) -> None:
    db.responses = {"rest.ddu_flat_info": []}

    response = await client.request(method, _url("ddu-flat-info"), headers=_auth())

    assert response.status_code == 200


async def test_cursor_like_value_is_an_ordinary_parameter(
    client: AsyncClient, db: FakeConnection
) -> None:
    db.responses = {"rest.ddu_flat_info": []}

    await client.get(_url("ddu-flat-info"), params={"flat_guid": "@cur"}, headers=_auth())

    assert db.calls == [("rest.ddu_flat_info", ("@cur",))]


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (
            "abc",
            "Произошла необработанная ошибка в БД SQLSTATE[22P02]: Invalid text representation: "
            '7 ERROR:  invalid input syntax for type integer: "abc"',
        ),
        (
            "99999999999",
            "Произошла необработанная ошибка в БД SQLSTATE[22003]: Numeric value out of range: "
            '7 ERROR:  value "99999999999" is out of range for type integer',
        ),
    ],
)
async def test_client_request_id_cast_errors_read_as_in_php(
    client: AsyncClient, db: FakeConnection, value: str, message: str
) -> None:
    response = await client.get(
        _url("ddu-request-info"),
        params={"client_request_id": value},
        headers=_auth(),
    )

    assert response.status_code == 500
    assert response.json() == _error(message)
    assert db.calls == []


async def test_response_bytes_and_headers_match_php(
    client: AsyncClient, db: FakeConnection
) -> None:
    db.responses = {"rest.ddu_flat_info": [{"url": "/doc/1", "name": "Кухня", "price": 10.5}]}

    response = await client.get(_url("ddu-flat-info"), headers=_auth())

    assert (
        response.content
        == (
            '{"data":{"url":"\\/doc\\/1","name":"Кухня","price":"10.5"},'
            '"response":true,"error":{"message":""}}'
        ).encode()
    )
    assert response.headers["content-type"] == "application/json"
    assert response.headers["cache-control"] == "no-cache, must-revalidate, max-age=0"


async def test_unhandled_function_error_has_php_prefix(
    client: AsyncClient, db: FakeConnection
) -> None:
    db.responses = {"rest.ddu_flat_info": UndefinedFunctionError("function is missing")}

    response = await client.get(_url("ddu-flat-info"), headers=_auth())

    assert response.status_code == 500
    assert response.json() == _error(
        "Произошла необработанная ошибка в БД SQLSTATE[42883]: Undefined function: "
        "7 ERROR:  function is missing",
    )


async def test_unhandled_function_error_keeps_libpq_context(
    client: AsyncClient, db: FakeConnection
) -> None:
    error = UndefinedFunctionError("function is missing")
    error.context = "PL/pgSQL function rest.ddu_flat_info line 3"
    db.responses = {"rest.ddu_flat_info": error}

    response = await client.get(_url("ddu-flat-info"), headers=_auth())

    assert response.json()["error"]["message"].endswith(
        "function is missing\nCONTEXT:  PL/pgSQL function rest.ddu_flat_info line 3",
    )


async def test_lookup_returns_function_error_text(client: AsyncClient, db: FakeConnection) -> None:
    db.responses = {"rest.ddu_flat_info": RaiseError("{Квартира не найдена}")}

    response = await client.get(_url("ddu-flat-info"), headers=_auth())

    assert response.status_code == 500
    assert response.json() == _error("Квартира не найдена")
