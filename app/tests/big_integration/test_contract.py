"""Frozen HTTP contract of the BIG Integration routes that production nginx sends here.

office.smartremont.kz/integration/<route> → /api/big_integration/<route>. BI Group
parses these responses: status codes, envelope keys, error texts and JSON value
types (Decimal → number, datetime → ISO string) must not change. A failing test
here means a breaking change for BI Group, not a test to update.

The database is replaced by a fake asyncpg connection, so the whole stack runs:
auth → router → service → repository → ``call_sp`` → error mapping.
"""

import base64
import re
from collections.abc import AsyncIterator, Callable
from datetime import datetime
from decimal import Decimal
from typing import Any

import pytest
from asyncpg.exceptions import RaiseError
from httpx import ASGITransport, AsyncClient

from src.config import big_integration_auth_config
from src.database.deps import get_db_connection
from src.main import app

_FUNCTION_RE = re.compile(r"^select ([a-z0-9_.]+)\(")
_CURSOR_RE = re.compile(r'^fetch all in "(.+)"$')

type SpResponse = list[dict[str, Any]] | Exception | Callable[[tuple[Any, ...]], Any]


class FakeConnection:
    """Answers stored-function calls the way asyncpg does, from a per-test script."""

    def __init__(self, responses: dict[str, SpResponse]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self._cursors: dict[str, list[dict[str, Any]]] = {}

    def _answer(self, function: str, args: tuple[Any, ...]) -> list[dict[str, Any]]:
        self.calls.append((function, args))
        response = self.responses[function]
        if isinstance(response, Exception):
            raise response
        if callable(response):
            return response(args)
        return response

    async def fetchval(self, query: str, *args: Any) -> None:
        assert query.startswith("select set_config(")

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        cursor = _CURSOR_RE.match(query)
        if cursor:
            return self._cursors.pop(cursor.group(1))
        function = _FUNCTION_RE.match(query).group(1)
        return self._answer(function, args)

    async def execute(self, query: str, *args: Any) -> None:
        function = _FUNCTION_RE.match(query).group(1)
        cursor_name, *sp_args = args
        self._cursors[cursor_name] = self._answer(function, tuple(sp_args))


@pytest.fixture
def db() -> FakeConnection:
    return FakeConnection({})


@pytest.fixture
async def client(db: FakeConnection) -> AsyncIterator[AsyncClient]:
    async def _connection() -> AsyncIterator[FakeConnection]:
        yield db

    app.dependency_overrides[get_db_connection] = _connection
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
    app.dependency_overrides.clear()


def _auth(password: str | None = None) -> dict[str, str]:
    user = big_integration_auth_config.username
    secret = password if password is not None else big_integration_auth_config.password
    token = base64.b64encode(f"{user}:{secret}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _ok(data: Any) -> dict[str, Any]:
    return {"data": data, "response": True, "error": {"message": ""}}


def _error(message: str) -> dict[str, Any]:
    return {"data": None, "response": False, "error": {"message": message}}


REQUEST_ROW = {
    "client_request_id": 2916069,
    "application_id": None,
    "order_id": "318796",
    "deal_id": None,
    "price": Decimal("1250000.50"),
    "area": Decimal("54"),
    "created_at": datetime(2026, 10, 20, 9, 30, 15),
}
REQUEST_ROW_JSON = {
    "client_request_id": 2916069,
    "application_id": None,
    "order_id": "318796",
    "deal_id": None,
    "price": 1250000.5,
    "area": 54,
    "created_at": "2026-10-20T09:30:15",
}


# --- auth and body parsing (shared by all routes) ---------------------------------


@pytest.mark.parametrize(
    "route",
    [
        "request-create-v3",
        "request-event-v3",
        "repair-pack-prices",
        "ddu-flat-info-multiple",
        "ddu-resident-agreement-status",
    ],
)
async def test_route_requires_basic_auth(client: AsyncClient, route: str) -> None:
    url = f"/api/big_integration/{route}"
    assert (await client.post(url, json={})).status_code == 401
    assert (await client.post(url, json={}, headers=_auth("wrong"))).status_code == 401


@pytest.mark.parametrize("body", [b"{not json", b"[1, 2]", b'"text"'])
async def test_body_must_be_a_json_object(client: AsyncClient, body: bytes) -> None:
    response = await client.post(
        "/api/big_integration/request-event-v3",
        content=body,
        headers={**_auth(), "Content-Type": "application/json"},
    )
    assert response.status_code == 400
    assert response.json() == _error("Invalid JSON")


# --- request-create-v3 / request-event-v3 -----------------------------------------


@pytest.mark.parametrize(
    ("route", "write_function"),
    [
        ("request-create-v3", "rest.ddu__create_request_v2"),
        ("request-event-v3", "rest.ddu__request_event_v3"),
    ],
)
async def test_request_write_returns_request_row(
    client: AsyncClient, db: FakeConnection, route: str, write_function: str
) -> None:
    db.responses = {
        write_function: [{write_function.split(".")[1]: 2916069}],
        "rest.ddu__request_get": [REQUEST_ROW],
    }
    body = {"order_id": "318796", "status": "DEAL_CREATED"}

    response = await client.post(f"/api/big_integration/{route}", json=body, headers=_auth())

    assert response.status_code == 200
    assert response.json() == _ok(REQUEST_ROW_JSON)
    assert db.calls[0] == (write_function, ('{"order_id": "318796", "status": "DEAL_CREATED"}',))
    assert db.calls[1] == ("rest.ddu__request_get", (2916069,))


@pytest.mark.parametrize(
    ("route", "write_function"),
    [
        ("request-create-v3", "rest.ddu__create_request_v2"),
        ("request-event-v3", "rest.ddu__request_event_v3"),
    ],
)
async def test_request_write_returns_function_error_text(
    client: AsyncClient, db: FakeConnection, route: str, write_function: str
) -> None:
    db.responses = {write_function: RaiseError('{Поле "client_request_id" не заполнено [ДДУ]}')}

    response = await client.post(f"/api/big_integration/{route}", json={}, headers=_auth())

    assert response.status_code == 500
    assert response.json() == _error('Поле "client_request_id" не заполнено [ДДУ]')


async def test_request_write_without_id_is_an_error(
    client: AsyncClient, db: FakeConnection
) -> None:
    db.responses = {"rest.ddu__request_event_v3": [{"ddu__request_event_v3": None}]}

    response = await client.post("/api/big_integration/request-event-v3", json={}, headers=_auth())

    assert response.status_code == 500
    assert response.json() == _error("Пустой ответ от rest.ddu__request_event_v3")


async def test_request_without_row_returns_null_data(
    client: AsyncClient, db: FakeConnection
) -> None:
    db.responses = {
        "rest.ddu__create_request_v2": [{"ddu__create_request_v2": 7}],
        "rest.ddu__request_get": [],
    }

    response = await client.post("/api/big_integration/request-create-v3", json={}, headers=_auth())

    assert response.status_code == 200
    assert response.json() == _ok(None)


# --- repair-pack-prices -----------------------------------------------------------

PACK_ROW = {
    "preset_guid": "p-1",
    "preset_name": "Комфорт",
    "is_old_repair_package": False,
    "preset_price": Decimal("4500000"),
    "optimal_price": Decimal("4300000.75"),
    "optimal_preset_guid": "p-2",
    "optimal_preset_name": "Оптимальный",
}


def _not_found_row(placement_uuid: str, message: str) -> dict[str, Any]:
    return {
        "placementUUID": placement_uuid,
        "preset_guid": None,
        "preset_name": None,
        "is_old_repair_package": None,
        "preset_price": None,
        "optimal_price": None,
        "optimal_preset_guid": None,
        "optimal_preset_name": None,
        "status": False,
        "error": message,
    }


async def test_repair_pack_prices_reports_each_placement(
    client: AsyncClient, db: FakeConnection
) -> None:
    def pack_info(args: tuple[Any, ...]) -> list[dict[str, Any]]:
        placement_uuid = args[0]
        if placement_uuid == "found":
            return [PACK_ROW]
        if placement_uuid == "broken":
            raise RaiseError("{Планировка не найдена}")
        return []

    db.responses = {"rest.ddu_repair_pack_info__get": pack_info}

    response = await client.post(
        "/api/big_integration/repair-pack-prices",
        json={"placementUUIDs": ["found", "missing", "broken", 42]},
        headers=_auth(),
    )

    assert response.status_code == 200
    assert response.json() == _ok(
        [
            {
                "preset_guid": "p-1",
                "preset_name": "Комфорт",
                "is_old_repair_package": False,
                "preset_price": 4500000,
                "optimal_price": 4300000.75,
                "optimal_preset_guid": "p-2",
                "optimal_preset_name": "Оптимальный",
                "placementUUID": "found",
                "status": True,
                "error": None,
            },
            _not_found_row("missing", "Подходящий пакет не найден"),
            _not_found_row("broken", "Планировка не найдена"),
            _not_found_row("42", "Подходящий пакет не найден"),
        ]
    )


async def test_repair_pack_prices_without_field_returns_empty_list(client: AsyncClient) -> None:
    response = await client.post(
        "/api/big_integration/repair-pack-prices", json={}, headers=_auth()
    )
    assert response.status_code == 200
    assert response.json() == _ok([])


async def test_repair_pack_prices_rejects_non_list(client: AsyncClient) -> None:
    response = await client.post(
        "/api/big_integration/repair-pack-prices",
        json={"placementUUIDs": "abc"},
        headers=_auth(),
    )
    assert response.status_code == 400
    assert response.json() == _error('Поле "placementUUIDs" имеет некорректный формат')


async def test_repair_pack_prices_limits_batch_size(client: AsyncClient) -> None:
    response = await client.post(
        "/api/big_integration/repair-pack-prices",
        json={"placementUUIDs": ["x"] * 10_001},
        headers=_auth(),
    )
    assert response.status_code == 403
    assert response.json() == _error('Слишком много элементов в поле "placementUUIDs"')


# --- ddu-flat-info-multiple -------------------------------------------------------


async def test_flat_info_multiple_returns_rows(client: AsyncClient, db: FakeConnection) -> None:
    db.responses = {
        "rest.ddu_flat_info_multiple": [
            {"flat_guid": "f-1", "area": Decimal("61.4"), "rooms": 2},
            {"flat_guid": "f-2", "area": Decimal("40"), "rooms": 1},
        ]
    }

    response = await client.post(
        "/api/big_integration/ddu-flat-info-multiple",
        json={"flat_guids": ["f-1", "f-2"]},
        headers=_auth(),
    )

    assert response.status_code == 200
    assert response.json() == _ok(
        [
            {"flat_guid": "f-1", "area": 61.4, "rooms": 2},
            {"flat_guid": "f-2", "area": 40, "rooms": 1},
        ]
    )
    assert db.calls == [("rest.ddu_flat_info_multiple", ('{"flat_guids": ["f-1", "f-2"]}',))]


@pytest.mark.parametrize("body", [{}, {"flat_guids": []}, {"flat_guids": "f-1"}])
async def test_flat_info_multiple_requires_guids(client: AsyncClient, body: dict) -> None:
    response = await client.post(
        "/api/big_integration/ddu-flat-info-multiple", json=body, headers=_auth()
    )
    assert response.status_code == 400
    assert response.json() == _error('Поле "flat_guids" не заполнено')


async def test_flat_info_multiple_returns_function_error(
    client: AsyncClient, db: FakeConnection
) -> None:
    db.responses = {"rest.ddu_flat_info_multiple": RaiseError("{Ошибка выборки}")}

    response = await client.post(
        "/api/big_integration/ddu-flat-info-multiple",
        json={"flat_guids": ["f-1"]},
        headers=_auth(),
    )

    assert response.status_code == 500
    assert response.json() == _error("Ошибка выборки")


# --- ddu-resident-agreement-status ------------------------------------------------


async def test_resident_agreement_status_logs_and_returns_null_data(
    client: AsyncClient, db: FakeConnection
) -> None:
    db.responses = {"rest.ddu_resident_agreement__log_insert": [{"x": 1}]}
    body = {"resident_guid": "r-1", "status": "SIGNED"}

    response = await client.post(
        "/api/big_integration/ddu-resident-agreement-status", json=body, headers=_auth()
    )

    assert response.status_code == 200
    assert response.json() == _ok(None)
    assert db.calls == [
        (
            "rest.ddu_resident_agreement__log_insert",
            ('{"resident_guid": "r-1", "status": "SIGNED"}',),
        )
    ]


async def test_resident_agreement_status_returns_function_error(
    client: AsyncClient, db: FakeConnection
) -> None:
    db.responses = {"rest.ddu_resident_agreement__log_insert": RaiseError("{Жилец не найден}")}

    response = await client.post(
        "/api/big_integration/ddu-resident-agreement-status", json={}, headers=_auth()
    )

    assert response.status_code == 500
    assert response.json() == _error("Жилец не найден")
