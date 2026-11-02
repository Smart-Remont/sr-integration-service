"""``request-constructives``, moved from PHP."""

from httpx import AsyncClient

from tests.big_integration.fakes import FLAT, FakeConnection, _auth, _error, _ok, _url


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
