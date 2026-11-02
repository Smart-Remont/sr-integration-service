"""BIG routes moved from PHP. Expected bodies were checked against the PHP dev stand.

The fake connection answers stored-function calls, so the whole stack runs, including the
exception handlers that must both roll the request back and keep the PHP envelope.
"""

import json
from collections.abc import AsyncIterator
from datetime import datetime
from decimal import Decimal
from typing import Any

import pytest
from asyncpg.exceptions import RaiseError, UndefinedFunctionError
from httpx import AsyncClient

from src.database.deps import get_db_connection
from src.main import app
from tests.big_integration.test_contract import FakeConnection, _auth, _error, _ok, client, db

__all__ = ("client", "db")

FLAT = "c0f17482-ca65-4e58-8446-d1b155f45945"
PRESET_ROUTE = "/api/big_integration/remont-preset"
LIST_ROUTE = "/api/big_integration/remont-preset-list-v2"


def _url(route: str) -> str:
    return f"/api/big_integration/{route}"


@pytest.mark.parametrize(
    ("method", "route"),
    [
        ("GET", "request-info"),
        ("GET", "request-status-info"),
        ("GET", "ddu-request-info"),
        ("GET", "ddu-flat-info"),
        ("POST", "request-constructives"),
        ("POST", "big-notify-client"),
        ("POST", "remont-preset-list-v2"),
        ("POST", "remont-preset"),
        ("GET", "remont-preset"),
    ],
)
async def test_route_requires_basic_auth(client: AsyncClient, method: str, route: str) -> None:
    for headers in ({}, _auth("wrong"), {"Authorization": "Basic not-base64"}):
        response = await client.request(method, _url(route), headers=headers)
        assert response.status_code == 401
        assert response.content == b""
        assert response.headers["www-authenticate"] == 'Basic realm="Access denied"'
        assert response.headers["content-type"] == "text/html; charset=UTF-8"


# --- short lookups ---------------------------------------------------------------


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


# --- request-constructives --------------------------------------------------------


async def test_constructives_builds_response_and_decodes_characteristics(
    client: AsyncClient, db: FakeConnection
) -> None:
    db.responses = {
        "rest.ddu_client_material__read": [{"material_id": 1}],
        "rest.ddu_client_filling__read": [
            {"filling_id": 1, "characteristic_json": '{"color": "белый"}'},
            {"filling_id": 2, "characteristic_json": "{broken"},
            {"filling_id": 3, "characteristic_json": None},
            {"filling_id": 4, "characteristic_json": '{"size": 1.5, "tags": {}}'},
        ],
        "rest.ddu_request_full_info": [
            {"flat_guid": FLAT, "preset_kit_id": 428, "preset_kit_name": "BIG+"},
        ],
    }

    response = await client.post(
        _url("request-constructives"),
        json={"placementUUID": FLAT},
        headers=_auth(),
    )

    assert response.status_code == 200
    assert response.json() == _ok(
        {
            "placementUUID": FLAT,
            "preset_id": 428,
            "preset_name": "BIG+",
            "material_list": [{"material_id": 1}],
            "filling_list": [
                {"filling_id": 1, "characteristic_json": {"color": "белый"}},
                {"filling_id": 2, "characteristic_json": None},
                {"filling_id": 3, "characteristic_json": None},
                {"filling_id": 4, "characteristic_json": {"size": 1.5, "tags": []}},
            ],
        },
    )


async def test_constructives_passes_broken_json_as_null(
    client: AsyncClient, db: FakeConnection
) -> None:
    db.responses = {
        "rest.ddu_client_material__read": [],
        "rest.ddu_client_filling__read": [],
        "rest.ddu_request_full_info": [],
    }

    response = await client.post(
        _url("request-constructives"),
        content=b"{broken",
        headers=_auth(),
    )

    assert response.status_code == 200
    assert response.json()["data"]["placementUUID"] is None
    assert db.calls[0] == ("rest.ddu_client_material__read", ("null",))


async def test_constructives_rejects_other_methods(client: AsyncClient) -> None:
    response = await client.get(_url("request-constructives"), headers=_auth())

    assert response.status_code == 405
    assert response.json() == _error("Неподдерживаемый метод")


# --- remont-preset-list-v2 / remont-preset ------------------------------------------

SECTIONS = [
    {
        "section_id": 1,
        "section_name": "Комнаты",
        "section_name_kz": "Бөлмелер",
        "section_show_type_code": "CONSTRUCTOR_TAB_ROOM_WORK_SET",
        "section_extra": "x",
    },
    {"section_id": 2, "section_show_type_code": "CONSTRUCTOR_TAB_WORK_SET"},
    {"section_id": 3, "section_show_type_code": "CONSTRUCTOR_TAB_ROOM", "section_extra": "y"},
    {"section_id": 4, "section_show_type_code": "FILLING"},
    {"section_id": 5, "section_show_type_code": "UNKNOWN"},
]
MATERIAL = {
    "preset_material_id": 50,
    "material_set_id": 60,
    "set_name": "Набор",
    "set_price": Decimal("100.50"),
    "set_extra": "z",
}
DETAILED_MATERIAL = {
    "preset_material_id": 50,
    "set_name": "Набор",
    "set_price": "100.50",
    "set_extra": "z",
    "features": [{"feature": 1}],
    "items": [{"item": 1}],
    "photos": [{"photo": 1}],
}
COMPACT_MATERIAL = {"preset_material_id": 50, "set_name": "Набор", "set_price": "100.50"}
KIT = {
    "preset_id": 428,
    "preset_name": "BIG+",
    "preset_price": Decimal("11871723"),
    "preset_guid": "g",
    "preset_extra": "k",
}


def _preset_functions() -> dict[str, Any]:
    return {
        "rest.ddu__preset_list_v2": [KIT],
        "rest.render_plan_room__read": [
            {"room_id": 10, "room_name": "Кухня", "room_name_kz": "Ас", "room_code": "K", "x": 1},
            {"room_id": -2, "room_name": "Скрытая", "room_name_kz": "", "room_code": "H", "x": 2},
        ],
        "rest.render_preset_feature_p_category__read": [{"feature_category_pid": 9}],
        "rest.render_preset_feature__read": [{"feature": "пол"}],
        "rest.render_filling__section__read": SECTIONS,
        "rest.render_filling__room_read": [{"room_id": 10}],
        "rest.render_filling__work_set_read": [{"work_set_id": 20}],
        "rest.render_filling__material_set_read": [MATERIAL],
        "rest.render_filling_features__read": [{"feature": 1}],
        "rest.render_filling__set_item_read": [{"item": 1}],
        "rest.render_set_photo__read": [{"photo": 1}],
        "rest.render__read": lambda args: [{"view": args[3]}],
    }


def _section(section_id: int) -> dict[str, Any]:
    return next(section for section in SECTIONS if section["section_id"] == section_id)


async def test_preset_list_returns_full_tree(client: AsyncClient, db: FakeConnection) -> None:
    db.responses = _preset_functions()

    response = await client.post(LIST_ROUTE, json={"placementUUID": FLAT}, headers=_auth())

    assert response.status_code == 200
    work_sets = [{"work_set_id": 20, "sets": [DETAILED_MATERIAL]}]
    room = {"room_id": 10, "work_sets": work_sets}
    head = {"room_id": 10, "room_name": "Кухня", "room_name_kz": "Ас", "room_code": "K"}
    assert response.json() == _ok(
        {
            "placementUUID": FLAT,
            "preset_list": [
                {
                    **KIT,
                    "preset_price": "11871723",
                    "features": [{"feature_category_pid": 9, "items": [{"feature": "пол"}]}],
                    "additional": [
                        {**_section(1), "rooms": [room]},
                        {**_section(2), "work_sets": work_sets},
                        {**_section(3), "rooms": [room]},
                        {**_section(4), "sets": [DETAILED_MATERIAL]},
                    ],
                    "renders": {
                        "clean": {"rooms": [{**head, "views": [{"view": 0}]}]},
                        "clean_with_filling": {"rooms": [{**head, "views": [{"view": 1}]}]},
                    },
                },
            ],
        },
    )
    assert db.calls[0] == ("rest.ddu__preset_list_v2", (json.dumps({"placementUUID": FLAT}),))
    assert ("rest.render_set_photo__read", (60,)) in db.calls


async def test_preset_price_route_keeps_php_leftover_section_fields(
    client: AsyncClient, db: FakeConnection
) -> None:
    db.responses = {
        **_preset_functions(),
        "rest.render_filling__section__read": [
            {"section_id": 4, "section_show_type_code": "FILLING", "only_in_first": 1},
            _section(1),
        ],
    }

    response = await client.post(
        PRESET_ROUTE,
        json={"placementUUID": FLAT, "preset_id": 428},
        headers=_auth(),
    )

    second = response.json()["data"]["preset_list"][0]["additional"][1]
    assert second["only_in_first"] == 1
    assert second["sets"] == [COMPACT_MATERIAL]
    assert "section_extra" not in second


async def test_preset_price_route_returns_compact_tree(
    client: AsyncClient, db: FakeConnection
) -> None:
    db.responses = _preset_functions()

    response = await client.post(
        PRESET_ROUTE,
        json={"placementUUID": FLAT, "preset_id": 428},
        headers=_auth(),
    )

    assert response.status_code == 200
    work_sets = [{"work_set_id": 20, "sets": [COMPACT_MATERIAL]}]
    room = {"room_id": 10, "work_sets": work_sets}
    compact_head = {key: value for key, value in _section(1).items() if key != "section_extra"}
    assert response.json() == _ok(
        {
            "placementUUID": FLAT,
            "preset_list": [
                {
                    "preset_id": 428,
                    "preset_name": "BIG+",
                    "preset_price": "11871723",
                    "preset_guid": "g",
                    "additional": [
                        {**compact_head, "rooms": [room]},
                        {**_section(2), "work_sets": work_sets},
                        {**_section(3), "rooms": [room]},
                        {**_section(4), "sets": [COMPACT_MATERIAL]},
                    ],
                },
            ],
        },
    )
    assert all(function != "rest.render_set_photo__read" for function, _ in db.calls)


@pytest.mark.parametrize("route", [LIST_ROUTE, PRESET_ROUTE])
@pytest.mark.parametrize(
    "content",
    [b"", b"{broken", b"[1]", b"{}", b'{"placementUUID": "  "}', b'{"placementUUID": "0"}'],
)
async def test_preset_routes_require_placement(
    client: AsyncClient, route: str, content: bytes
) -> None:
    response = await client.post(route, content=content, headers=_auth())

    assert response.status_code == 400
    assert response.json() == _error("ГУИД помещения не может быть пустым")


@pytest.mark.parametrize("preset_id", [None, 0, "", " ", "0"])
async def test_preset_price_route_requires_preset(client: AsyncClient, preset_id: object) -> None:
    response = await client.post(
        PRESET_ROUTE,
        json={"placementUUID": FLAT, "preset_id": preset_id},
        headers=_auth(),
    )

    assert response.status_code == 400
    assert response.json() == _error("preset_id не может быть пустым")


@pytest.mark.parametrize("route", [LIST_ROUTE, PRESET_ROUTE])
async def test_preset_routes_reject_other_methods(client: AsyncClient, route: str) -> None:
    response = await client.get(route, headers=_auth())

    assert response.status_code == 405
    assert response.json() == _error("Неподдерживаемый метод")


async def test_nested_function_error_fails_the_whole_response(
    client: AsyncClient, db: FakeConnection
) -> None:
    db.responses = {
        **_preset_functions(),
        "rest.render__read": RaiseError("{Рендер недоступен}"),
    }

    response = await client.post(LIST_ROUTE, json={"placementUUID": FLAT}, headers=_auth())

    assert response.status_code == 500
    assert response.json() == _error("Рендер недоступен")


# --- big-notify-client ------------------------------------------------------------


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
