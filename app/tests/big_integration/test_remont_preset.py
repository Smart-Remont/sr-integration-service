"""``remont-preset-list-v2`` and ``remont-preset``, moved from PHP."""

from decimal import Decimal
from typing import Any

import pytest
from asyncpg.exceptions import RaiseError
from httpx import AsyncClient

from tests.big_integration.fakes import FLAT, FakeConnection, _auth, _error, _ok

PRESET_ROUTE = "/api/big_integration/remont-preset"
LIST_ROUTE = "/api/big_integration/remont-preset-list-v2"

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
                        {**_section(3), "rooms": [{"room_id": 10, "sets": [DETAILED_MATERIAL]}]},
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
    assert db.calls[0] == ("rest.ddu__preset_list_v2", (f'{{"placementUUID":"{FLAT}"}}',))
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


def _only_sections(*sections: dict[str, Any]) -> dict[str, Any]:
    return {**_preset_functions(), "rest.render_filling__section__read": list(sections)}


@pytest.mark.parametrize("route", [LIST_ROUTE, PRESET_ROUTE])
async def test_work_set_section_keeps_only_the_last_work_set(
    client: AsyncClient, db: FakeConnection, route: str
) -> None:
    db.responses = {
        **_only_sections(_section(2)),
        "rest.render_filling__work_set_read": [{"work_set_id": 20}, {"work_set_id": 21}],
    }

    response = await client.post(
        route,
        json={"placementUUID": FLAT, "preset_id": 428},
        headers=_auth(),
    )

    work_sets = response.json()["data"]["preset_list"][0]["additional"][0]["work_sets"]
    assert [work_set["work_set_id"] for work_set in work_sets] == [21]


async def test_work_set_section_without_rows_is_a_list_with_null(
    client: AsyncClient, db: FakeConnection
) -> None:
    db.responses = {
        **_only_sections(_section(2)),
        "rest.render_filling__work_set_read": [],
    }

    response = await client.post(LIST_ROUTE, json={"placementUUID": FLAT}, headers=_auth())

    assert response.json()["data"]["preset_list"][0]["additional"][0]["work_sets"] == [None]


async def test_work_set_section_without_rows_repeats_the_earlier_work_set(
    client: AsyncClient, db: FakeConnection
) -> None:
    def work_sets(args: tuple[Any, ...]) -> list[dict[str, int]]:
        return [{"work_set_id": 20}] if args[2] == 10 else []

    db.responses = {
        **_only_sections(_section(1), _section(2)),
        "rest.render_filling__work_set_read": work_sets,
    }

    response = await client.post(LIST_ROUTE, json={"placementUUID": FLAT}, headers=_auth())

    second = response.json()["data"]["preset_list"][0]["additional"][1]
    assert [work_set["work_set_id"] for work_set in second["work_sets"]] == [20]


async def test_room_section_clears_the_sets_of_the_earlier_work_set(
    client: AsyncClient, db: FakeConnection
) -> None:
    def work_sets(args: tuple[Any, ...]) -> list[dict[str, int]]:
        return [{"work_set_id": 20}] if args[2] == 10 else []

    db.responses = {
        **_only_sections(_section(1), _section(3), _section(2)),
        "rest.render_filling__work_set_read": work_sets,
    }

    response = await client.post(LIST_ROUTE, json={"placementUUID": FLAT}, headers=_auth())

    assert response.json()["data"]["preset_list"][0]["additional"][2]["work_sets"] == [
        {"work_set_id": 20, "sets": []},
    ]


async def test_room_section_reads_materials_of_the_room_without_work_sets(
    client: AsyncClient, db: FakeConnection
) -> None:
    db.responses = _only_sections(_section(3))

    await client.post(LIST_ROUTE, json={"placementUUID": FLAT}, headers=_auth())

    assert ("rest.render_filling__material_set_read", (428, 3, 10, None, FLAT)) in db.calls
    assert all(function != "rest.render_filling__work_set_read" for function, _ in db.calls)


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
                        {**_section(3), "rooms": [{"room_id": 10, "sets": [COMPACT_MATERIAL]}]},
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


@pytest.mark.parametrize(("value", "bound"), [(2.0, "2"), (2.5, "2.5"), (123, "123"), (True, "1")])
async def test_placement_scalar_is_bound_as_php_text(
    client: AsyncClient, db: FakeConnection, value: object, bound: str
) -> None:
    db.responses = {"rest.ddu__preset_list_v2": [], "rest.render_plan_room__read": []}

    await client.post(LIST_ROUTE, json={"placementUUID": value}, headers=_auth())

    assert ("rest.render_plan_room__read", (bound,)) in db.calls


@pytest.mark.parametrize(
    "body",
    [
        b"\xef\xbb\xbf" + f'{{"placementUUID":"{FLAT}"}}'.encode(),
        f'{{"placementUUID":"{FLAT}","x":"\\ud83d"}}'.encode(),
        f'{{"placementUUID":"{FLAT}"}}'.encode("utf-16"),
        f'{{"placementUUID":"{FLAT}","x":NaN}}'.encode(),
        f'{{"placementUUID":"{FLAT}","x":{"[" * 511}{"]" * 511}}}'.encode(),
    ],
)
async def test_json_php_refuses_is_an_empty_placement(
    client: AsyncClient, db: FakeConnection, body: bytes
) -> None:
    response = await client.post(LIST_ROUTE, content=body, headers=_auth())

    assert response.status_code == 400
    assert "ГУИД помещения" in response.json()["error"]["message"]
    assert db.calls == []


@pytest.mark.parametrize(
    ("extra", "sent"),
    [
        ("1.0", "1"),
        ("1e3", "1000"),
        ("2.50", "2.5"),
        ("12345678901234567890", "1.2345678901234567e+19"),
        ('"a/b"', '"a\\/b"'),
        ('"кир"', '"\\u043a\\u0438\\u0440"'),
    ],
)
async def test_function_gets_the_text_json_encode_made(
    client: AsyncClient, db: FakeConnection, extra: str, sent: str
) -> None:
    # The function logs this text, and its database errors quote it.
    db.responses = {"rest.ddu__preset_list_v2": [], "rest.render_plan_room__read": []}

    response = await client.post(
        LIST_ROUTE,
        content=f'{{"placementUUID":"{FLAT}","x":{extra}}}'.encode(),
        headers=_auth(),
    )

    assert response.status_code == 200
    assert db.calls[0] == (
        "rest.ddu__preset_list_v2",
        (f'{{"placementUUID":"{FLAT}","x":{sent}}}',),
    )
