"""``albato-meta-lead``, moved from PHP: the same checks in the same order."""

import json

import pytest
from asyncpg.exceptions import DivisionByZeroError, RaiseError
from httpx import AsyncClient

from tests.albato_meta.conftest import CREATE, ROUTE, SETTING, TOKEN
from tests.big_integration.fakes import FakeConnection

LEAD = {"name": "Тест", "phone": "77000000001"}
PARAMS = {"token": TOKEN}


def _error(text: str) -> bytes:
    return ('{"error":' + json.dumps(text) + "}").encode()


async def test_lead_is_created_with_201(client: AsyncClient, db: FakeConnection) -> None:
    response = await client.post(ROUTE, params=PARAMS, json=LEAD)

    assert response.status_code == 201
    assert response.content == b'{"status":"ok","crm_deal_id":123}'
    assert response.headers["content-type"] == "application/json; charset=utf-8"
    assert response.headers["cache-control"] == "no-store, no-cache, must-revalidate"
    assert db.calls[-1] == (CREATE, ('{"name":"Тест","phone":"77000000001"}',))


async def test_values_are_filtered_and_json_is_compact(
    client: AsyncClient, db: FakeConnection
) -> None:
    body = {"name": " <b>А/Б</b> 😀", "phone": 77000000001, "x": None}

    response = await client.post(ROUTE, params=PARAMS, json=body)

    assert response.status_code == 201
    assert db.calls[-1] == (
        CREATE,
        ('{"name":"&lt;b&gt;А/Б&lt;/b&gt;","phone":"77000000001","x":""}',),
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"params": {"token": "wrong"}},
        {"params": {"token": ""}},
        {"params": {"token": TOKEN + "x"}},
        {"params": {"token[]": TOKEN}},
        {"params": {}},
        {"params": {"Token": TOKEN}},
    ],
)
async def test_wrong_token_is_401_and_creates_nothing(
    client: AsyncClient, db: FakeConnection, kwargs: dict[str, dict[str, str]]
) -> None:
    response = await client.post(ROUTE, json=LEAD, **kwargs)

    assert response.status_code == 401
    assert response.content == _error("Неверный токен")
    assert [call[0] for call in db.calls] == [SETTING]


async def test_token_in_the_address_and_in_a_form_as_in_zend(
    client: AsyncClient, db: FakeConnection
) -> None:
    in_address = await client.post(f"{ROUTE}/token/{TOKEN}", json=LEAD)
    in_form = await client.post(ROUTE, data={"token": TOKEN})
    address_wins = await client.post(f"{ROUTE}/token/wrong", params=PARAMS, json=LEAD)

    assert in_address.status_code == 201
    assert in_form.status_code == 400
    assert in_form.content == _error("Пустое или невалидное тело запроса")
    assert address_wins.status_code == 401


async def test_token_in_a_multipart_form_is_found_and_the_body_is_empty(
    client: AsyncClient,
) -> None:
    content = (
        f'--b\r\nContent-Disposition: form-data; name="token"\r\n\r\n{TOKEN}\r\n--b--\r\n'
    ).encode()

    response = await client.post(
        ROUTE,
        content=content,
        headers={"Content-Type": "multipart/form-data; boundary=b"},
    )

    assert response.status_code == 400
    assert response.content == _error("Пустое или невалидное тело запроса")


async def test_setting_is_trimmed(client: AsyncClient, db: FakeConnection) -> None:
    db.responses[SETTING] = [{"result": f"  {TOKEN}  "}]

    response = await client.post(ROUTE, params=PARAMS, json=LEAD)

    assert response.status_code == 201


@pytest.mark.parametrize("setting", ["", "   ", None])
async def test_empty_setting_is_503_and_does_nothing(
    client: AsyncClient, db: FakeConnection, setting: str | None
) -> None:
    db.responses[SETTING] = [{"result": setting}]

    response = await client.post(ROUTE, params={"token": ""}, json=LEAD)

    assert response.status_code == 503
    assert response.content == _error("Не задан токен интеграции")
    assert [call[0] for call in db.calls] == [SETTING]


async def test_setting_that_cannot_be_read_is_503(client: AsyncClient, db: FakeConnection) -> None:
    db.responses[SETTING] = DivisionByZeroError("x")

    response = await client.post(ROUTE, params=PARAMS, json=LEAD)

    assert response.status_code == 503
    assert [call[0] for call in db.calls] == [SETTING]


@pytest.mark.parametrize("method", ["GET", "PUT", "DELETE", "PATCH"])
async def test_other_method_is_405_after_the_token(
    client: AsyncClient, db: FakeConnection, method: str
) -> None:
    right = await client.request(method, ROUTE, params=PARAMS)
    wrong = await client.request(method, ROUTE, params={"token": "wrong"})

    assert right.status_code == 405
    assert right.content == _error("Метод не поддерживается")
    assert wrong.status_code == 401
    assert CREATE not in [call[0] for call in db.calls]


@pytest.mark.parametrize("body", [b"", b"{", b"null", b"5", b'"x"', b"true", b"1.5"])
async def test_body_that_is_not_an_array_is_400(client: AsyncClient, body: bytes) -> None:
    response = await client.post(ROUTE, params=PARAMS, content=body)

    assert response.status_code == 400
    assert response.content == _error("Пустое или невалидное тело запроса")


@pytest.mark.parametrize(
    ("body", "text"),
    [
        ({"phone": "7"}, "Поле name обязательно"),
        ({"name": None, "phone": "7"}, "Поле name обязательно"),
        ({"name": "  ", "phone": "7"}, "Поле name обязательно"),
        ({"name": False, "phone": "7"}, "Поле name обязательно"),
        ({"name": "x"}, "Поле phone обязательно"),
        ({"name": "x", "phone": "\t"}, "Поле phone обязательно"),
        (["x"], "Поле name обязательно"),
        ([], "Поле name обязательно"),
    ],
)
async def test_required_fields(client: AsyncClient, body: object, text: str) -> None:
    response = await client.post(ROUTE, params=PARAMS, json=body)

    assert response.status_code == 400
    assert response.content == _error(text)


@pytest.mark.parametrize("name", [0, True, ["x"], {"a": 1}, " "])
async def test_values_php_lets_through_reach_the_function(
    client: AsyncClient, db: FakeConnection, name: object
) -> None:
    response = await client.post(ROUTE, params=PARAMS, json={"name": name, "phone": "7"})

    assert response.status_code == 201
    assert db.calls[-1][0] == CREATE


@pytest.mark.parametrize(
    ("raised", "text"),
    [
        ("{Источник META_LEAD_ADS не найден или неактивен}", None),
        ("{}", "Не удалось создать лид"),
        ("{0}", "Не удалось создать лид"),
    ],
)
async def test_refusal_is_500_and_rolls_back(
    client: AsyncClient,
    db: FakeConnection,
    rollbacks: list[Exception],
    raised: str,
    text: str | None,
) -> None:
    db.responses[CREATE] = RaiseError(raised)

    response = await client.post(ROUTE, params=PARAMS, json=LEAD)

    assert response.status_code == 500
    assert response.content == _error(text or raised.strip("{}"))
    assert len(rollbacks) == 1


async def test_failure_text_is_answered_and_not_logged(
    client: AsyncClient, db: FakeConnection, caplog: pytest.LogCaptureFixture
) -> None:
    db.responses[CREATE] = DivisionByZeroError("тайна")

    response = await client.post(ROUTE, params=PARAMS, json=LEAD)

    assert response.status_code == 500
    assert "Произошла" in response.json()["error"]
    assert "тайна" not in caplog.text
