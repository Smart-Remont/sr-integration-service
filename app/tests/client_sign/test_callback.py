"""``client-sign/callback``. Never sent to the stand: it would record a signature."""

import asyncio
import json
from collections.abc import Iterator

import pytest
from asyncpg.exceptions import RaiseError, UniqueViolationError
from httpx import AsyncClient
from loguru import logger

from src.config import ClientSignConfig
from src.features.client_sign.callback_key import callback_key
from src.features.client_sign.errors import ClientSignDatabaseError
from src.features.client_sign.repo import ClientSignDatabase, sign_process_lock_key
from src.shared import cron_run
from tests.client_sign.fakes import (
    CONFIG,
    FIND_SIGN,
    INSERT_SIGN,
    LOCK,
    MODIFY_SIGN,
    FakeConnection,
    FakePool,
    Partners,
    install_overrides,
)

ROUTE = "/api/client_sign/callback"
PROCESS = "11111111-1111-1111-1111-111111111111"
GROUP = "22222222-2222-2222-2222-222222222222"
IIN = "123456789012"
ACCEPTED = b'{"status":true}'
DS_KEY = callback_key(CONFIG.callback_secret, "CLIENT_DS_SIGN", 5)


def signed_ds(**changes: object) -> dict[str, object]:
    return {
        "sign_process_id": PROCESS,
        "group_id": GROUP,
        "status": "SUCCESS",
        "ext_id": 5,
        "dn_name": f"CN=X,IIN{IIN}",
        "meta_data": {"type_code": "CLIENT_DS_SIGN", "doc_type": "DS", "ds_id": 5},
        **changes,
    }


def url_with_key(key: str = DS_KEY) -> str:
    return f"{ROUTE}/key/{key}"


def stored_row(insert: tuple[object, ...], sign_id: object) -> dict[str, object]:
    def number(value: object) -> int | None:
        return None if value is None else int(str(value))

    return {
        "sign_id": number(sign_id),
        "sign_type_code": insert[3],
        "agreement_client_request_id": number(insert[0]),
        "ds_id": number(insert[1]),
        "client_request_document_id": number(insert[2]),
    }


def recording_table(connection: FakeConnection) -> None:
    """``sign_tab``: what is committed is what the next callback finds."""

    def find(process: str) -> list[dict[str, object]]:
        for index, (function, args) in enumerate(connection.committed):
            if function == MODIFY_SIGN and args[1] == process:
                return [stored_row(connection.committed[index - 1][1], args[0])]
        return []

    connection.handlers = {INSERT_SIGN: 501, FIND_SIGN: find}


def error_json(text: str) -> bytes:
    return json.dumps({"status": False, "error": text}, separators=(",", ":")).encode()


@pytest.fixture
def captured_logs() -> Iterator[list[str]]:
    messages: list[str] = []
    sink = logger.add(messages.append, format="{message}")
    yield messages
    logger.remove(sink)


async def test_signature_is_recorded_as_php_records_it(
    client: AsyncClient, connection: FakeConnection, partners: Partners
) -> None:
    recording_table(connection)
    response = await client.post(url_with_key(), content=json.dumps(signed_ds()))
    assert response.status_code == 200
    assert response.content == ACCEPTED
    assert response.headers["content-type"] == "application/json"
    assert response.headers["pragma"] == "no-cache"
    assert connection.committed == [
        (
            INSERT_SIGN,
            (
                None,
                "5",
                None,
                "CLIENT_DS_SIGN",
                "SIGN_CMS",
                "SIGN_CMS",
                None,
                f"CN=X,IIN{IIN}",
                "MYNCA",
                None,
            ),
        ),
        (MODIFY_SIGN, ("501", PROCESS, GROUP, "CMS", None)),
    ]
    # Nothing is asked from MyNCA: its state during its own callback is unknown.
    assert partners.requests == []


@pytest.mark.parametrize(
    ("meta", "expected", "sign_type", "doc_id"),
    [
        ({"doc_type": "ds", "ds_id": 9}, (None, "9", None), "CLIENT_DS_SIGN", 9),
        ({"type_code": "CLIENT_DS_SIGN"}, (None, "5", None), "CLIENT_DS_SIGN", 5),
        ({"doc_type": "DS", "ds_id": 0}, (None, "5", None), "CLIENT_DS_SIGN", 5),
        (
            {"doc_type": "OTBASY_STATEMENT", "client_request_document_id": 7},
            (None, None, "7"),
            "OTBASY_STATEMENT_SIGN",
            7,
        ),
        (
            {"type_code": "CLIENT_SIGN", "client_request_id": "8"},
            ("8", None, None),
            "CLIENT_SIGN",
            8,
        ),
        ({"doc_type": "AGREEMENT"}, ("5", None, None), "CLIENT_SIGN", 5),
    ],
)
async def test_document_comes_from_meta_data_or_ext_id(
    client: AsyncClient,
    connection: FakeConnection,
    meta: dict[str, object],
    expected: tuple[str | None, ...],
    sign_type: str,
    doc_id: int,
) -> None:
    recording_table(connection)
    key = callback_key(CONFIG.callback_secret, sign_type, doc_id)
    response = await client.post(url_with_key(key), json=signed_ds(meta_data=meta))
    assert response.content == ACCEPTED
    insert = connection.called(INSERT_SIGN)[0]
    assert insert[:3] == expected
    assert insert[3] == sign_type


async def test_repeated_callback_writes_once_and_answers_success(
    client: AsyncClient, connection: FakeConnection
) -> None:
    recording_table(connection)
    first = await client.post(url_with_key(), json=signed_ds())
    second = await client.post(url_with_key(), json=signed_ds())
    assert first.content == second.content == ACCEPTED
    assert len(connection.called(INSERT_SIGN)) == 1
    assert len(connection.committed) == 2


async def test_lookup_goes_through_a_cursor_with_a_uuid_cast(
    client: AsyncClient, connection: FakeConnection
) -> None:
    recording_table(connection)
    await client.post(url_with_key(), json=signed_ds())
    assert connection.called(FIND_SIGN) == [(PROCESS,)]


async def test_lock_comes_before_the_lookup_and_the_write(
    client: AsyncClient, connection: FakeConnection
) -> None:
    recording_table(connection)
    await client.post(url_with_key(), json=signed_ds())
    names = [name for name, _ in connection.calls]
    assert names == [LOCK, FIND_SIGN, INSERT_SIGN, MODIFY_SIGN]
    assert connection.called(LOCK) == [(sign_process_lock_key(PROCESS),)]


async def test_retry_after_a_lost_commit_records_the_signature(
    client: AsyncClient, connection: FakeConnection
) -> None:
    recording_table(connection)
    connection.fail_commit = True
    first = await client.post(url_with_key(), json=signed_ds())
    assert first.status_code == 500
    assert connection.committed == []
    connection.fail_commit = False
    second = await client.post(url_with_key(), json=signed_ds())
    assert second.content == ACCEPTED
    assert len(connection.committed) == 2


async def test_refusal_of_the_database_rolls_back_and_answers_500(
    client: AsyncClient, connection: FakeConnection, captured_logs: list[str]
) -> None:
    text = f"Идентификатор ИИН:{IIN} не соответствует клиенту в заявке"
    recording_table(connection)
    connection.handlers[INSERT_SIGN] = RaiseError("{" + text + "}")
    response = await client.post(url_with_key(), json=signed_ds())
    assert response.status_code == 500
    assert response.content == error_json(f"Ошибка insert_sign_general: {text}")
    assert connection.committed == []
    assert not any(IIN in message for message in captured_logs)
    line = next(line for line in captured_logs if "client-sign callback refused" in line)
    assert "reason=database" in line
    assert PROCESS in line
    assert "type=CLIENT_DS_SIGN" in line
    assert "document=5" in line
    assert "error=P0001" in line


async def test_unique_index_error_keeps_php_text(
    client: AsyncClient, connection: FakeConnection
) -> None:
    recording_table(connection)
    connection.handlers[INSERT_SIGN] = UniqueViolationError("duplicate key")
    response = await client.post(url_with_key(), json=signed_ds())
    assert response.status_code == 500
    assert (
        "Ошибка insert_sign_general: Произошла необработанная ошибка в БД"
        in json.loads(response.content)["error"]
    )


async def test_failed_attach_rolls_back_so_no_row_lacks_its_process(
    client: AsyncClient, connection: FakeConnection, captured_logs: list[str]
) -> None:
    recording_table(connection)
    connection.handlers[MODIFY_SIGN] = RaiseError("{отказ}")
    response = await client.post(url_with_key(), json=signed_ds())
    assert response.status_code == 500
    assert json.loads(response.content)["error"] == "Ошибка sign_tab__modify: отказ"
    assert connection.committed == []
    assert any("reason=attach_failed" in line and "P0001" in line for line in captured_logs)

    recording_table(connection)
    assert (await client.post(url_with_key(), json=signed_ds())).content == ACCEPTED
    assert (await client.post(url_with_key(), json=signed_ds())).content == ACCEPTED
    assert len(connection.committed) == 2


async def test_key_of_a_with_process_of_b_does_not_hide_the_signature_of_b(
    client: AsyncClient, connection: FakeConnection, captured_logs: list[str]
) -> None:
    recording_table(connection)
    key_a = callback_key(CONFIG.callback_secret, "CLIENT_DS_SIGN", 6)
    forged = await client.post(
        url_with_key(key_a), json=signed_ds(meta_data={"doc_type": "DS", "ds_id": 6})
    )
    assert forged.content == ACCEPTED
    committed = list(connection.committed)

    real = await client.post(url_with_key(), json=signed_ds())
    assert real.status_code == 409
    assert json.loads(real.content) == {
        "status": False,
        "error": "Процесс подписания уже записан на другой документ",
    }
    assert connection.committed == committed
    assert len(connection.called(INSERT_SIGN)) == 1
    line = next(line for line in captured_logs if "process_on_other_document" in line)
    assert PROCESS in line
    assert "document=5" in line
    assert "stored_sign_id=501" in line


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        ({"sign_type_code": "CLIENT_DS_SIGN", "ds_id": 5}, 200),
        ({"sign_type_code": "CLIENT_DS_SIGN_SECOND", "ds_id": 5}, 200),
        ({"sign_type_code": "CLIENT_DS_SIGN", "ds_id": 6}, 409),
        ({"sign_type_code": "CLIENT_SIGN", "agreement_client_request_id": 5}, 409),
        ({"sign_type_code": "CONTRACTOR_AGREEMENT_SIGN", "ds_id": 5}, 409),
        ({"sign_type_code": "CLIENT_DS_SIGN", "ds_id": None}, 409),
    ],
)
async def test_repeat_compares_the_stored_type_and_document(
    client: AsyncClient,
    connection: FakeConnection,
    stored: dict[str, object],
    expected: int,
) -> None:
    connection.handlers = {FIND_SIGN: [{"sign_id": 9, **stored}]}
    response = await client.post(url_with_key(), json=signed_ds())
    assert response.status_code == expected
    assert connection.called(INSERT_SIGN) == []


@pytest.mark.parametrize("group", [{"group_id": ""}, {"group_id": None}, {"group_id": False}, {}])
async def test_empty_group_is_null_and_the_repeat_finds_the_process(
    client: AsyncClient, connection: FakeConnection, group: dict[str, object]
) -> None:
    recording_table(connection)
    body = {key: value for key, value in signed_ds().items() if key != "group_id"} | group
    assert (await client.post(url_with_key(), json=body)).content == ACCEPTED
    assert connection.called(MODIFY_SIGN) == [("501", PROCESS, None, "CMS", None)]
    assert (await client.post(url_with_key(), json=body)).content == ACCEPTED
    assert len(connection.called(INSERT_SIGN)) == 1


MALFORMED_UUIDS = [
    "abc",
    " ",
    "11111111111111111111111111111111",
    f"urn:uuid:{PROCESS}",
    f"{{{PROCESS}}}",
    f"{PROCESS}\n",
    "1111-1111-1111-1111-1111-1111-1111-1111",
]


@pytest.mark.parametrize("value", MALFORMED_UUIDS)
async def test_malformed_group_writes_nothing_and_a_good_retry_records_once(
    client: AsyncClient, connection: FakeConnection, captured_logs: list[str], value: str
) -> None:
    recording_table(connection)
    response = await client.post(url_with_key(), json=signed_ds(group_id=value))
    assert response.status_code == 422
    assert json.loads(response.content)["error"] == "group_id не UUID"
    assert connection.calls == []
    line = next(line for line in captured_logs if "invalid_group_id" in line)
    assert PROCESS in line
    assert "type=CLIENT_DS_SIGN" in line
    assert "document=5" in line

    assert (await client.post(url_with_key(), json=signed_ds())).content == ACCEPTED
    assert (await client.post(url_with_key(), json=signed_ds())).content == ACCEPTED
    assert len(connection.called(INSERT_SIGN)) == 1
    assert connection.called(MODIFY_SIGN) == [("501", PROCESS, GROUP, "CMS", None)]


@pytest.mark.parametrize("value", [*MALFORMED_UUIDS, "", 5, ["a"]])
async def test_malformed_process_writes_nothing_and_does_not_reach_the_log(
    client: AsyncClient, connection: FakeConnection, captured_logs: list[str], value: object
) -> None:
    recording_table(connection)
    response = await client.post(url_with_key(), json=signed_ds(sign_process_id=value))
    assert response.status_code == 422
    assert json.loads(response.content)["error"] == "sign_process_id не UUID"
    assert connection.calls == []
    line = next(line for line in captured_logs if "invalid_process_id" in line)
    assert "process=not-a-uuid" in line


async def test_uppercase_uuid_is_accepted(client: AsyncClient, connection: FakeConnection) -> None:
    recording_table(connection)
    process = "ABCDEF00-1111-1111-1111-111111111111"
    response = await client.post(url_with_key(), json=signed_ds(sign_process_id=process))
    assert response.content == ACCEPTED


async def test_malformed_values_without_the_key_get_403_first(
    client: AsyncClient, connection: FakeConnection
) -> None:
    response = await client.post(ROUTE, json=signed_ds(sign_process_id="abc", group_id="abc"))
    assert response.status_code == 403
    assert connection.calls == []


@pytest.mark.parametrize(
    "url",
    [ROUTE, f"{ROUTE}/key/wrong", f"{ROUTE}?key={DS_KEY}", f"{ROUTE}/key/a/key/b"],
)
async def test_bad_sender_gets_403_and_writes_nothing(
    client: AsyncClient, connection: FakeConnection, url: str
) -> None:
    recording_table(connection)
    response = await client.post(url, json=signed_ds())
    assert response.status_code == 403
    assert json.loads(response.content) == {
        "status": False,
        "error": "Колбэк не от MyNCA: неверный ключ в адресе",
    }
    assert connection.calls == []


async def test_key_of_one_document_does_not_open_another(
    client: AsyncClient, connection: FakeConnection
) -> None:
    recording_table(connection)
    body = signed_ds(meta_data={"doc_type": "DS", "ds_id": 6})
    response = await client.post(url_with_key(), json=body)
    assert response.status_code == 403
    assert connection.calls == []


async def test_non_ascii_key_is_refused_and_does_not_crash(
    client: AsyncClient, connection: FakeConnection
) -> None:
    response = await client.post(f"{ROUTE}/key/%D1%84%ff", json=signed_ds())
    assert response.status_code == 403


async def test_missing_secret_gives_503_for_any_body(
    client: AsyncClient, connection: FakeConnection, partners: Partners
) -> None:
    install_overrides(connection, partners, ClientSignConfig())
    response = await client.post(url_with_key(), json=signed_ds())
    assert response.status_code == 503
    assert connection.calls == []


@pytest.mark.parametrize("status_value", ["ERROR", "EXPIRED", "success", "", None, 1, ["SUCCESS"]])
async def test_other_statuses_are_accepted_without_a_write_or_a_key(
    client: AsyncClient, connection: FakeConnection, status_value: object
) -> None:
    response = await client.post(ROUTE, json=signed_ds(status=status_value))
    assert response.status_code == 200
    assert response.content == ACCEPTED
    assert connection.calls == []


async def test_unknown_document_type_is_accepted_without_a_write(
    client: AsyncClient, connection: FakeConnection
) -> None:
    response = await client.post(ROUTE, json=signed_ds(meta_data={"doc_type": "OTHER"}))
    assert response.content == ACCEPTED
    assert connection.calls == []


NO_PROCESS = b'{"status":false,"error":"sign_process_id \\u043d\\u0435 \\u0443\\u043a\\u0430\\u0437\\u0430\\u043d"}'
BAD_JSON = (
    b'{"status":false,"error":"\\u041d\\u0435\\u0432\\u0430\\u043b\\u0438\\u0434\\u043d\\u044b\\u0439'
    b' JSON \\u0432 callback"}'
)


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (b"", BAD_JSON),
        (b"not json", BAD_JSON),
        (b'"text"', BAD_JSON),
        (b"12", BAD_JSON),
        (b"\xff", BAD_JSON),
        (b"[]", NO_PROCESS),
        (b"{}", NO_PROCESS),
        (b'{"sign_process_id":null}', NO_PROCESS),
        (b'["a"]', NO_PROCESS),
    ],
)
async def test_bad_bodies_answer_500_with_php_bytes(
    client: AsyncClient, connection: FakeConnection, body: bytes, expected: bytes
) -> None:
    response = await client.post(ROUTE, content=body)
    assert response.status_code == 500
    assert response.content == expected
    assert connection.calls == []


async def test_get_reaches_the_same_action_with_an_empty_body(client: AsyncClient) -> None:
    response = await client.get(ROUTE)
    assert response.status_code == 500
    assert response.content == BAD_JSON


async def test_nothing_personal_reaches_the_log(
    client: AsyncClient, connection: FakeConnection, captured_logs: list[str]
) -> None:
    recording_table(connection)
    await client.post(url_with_key(), json=signed_ds())
    await client.post(f"{ROUTE}/key/wrong", json=signed_ds(dn_name=f"IIN{IIN}"))
    await client.post(url_with_key(), json=signed_ds(sign_process_id=f"IIN{IIN}"))
    await client.post(ROUTE, json=signed_ds(sign_process_id=f"IIN{IIN}", meta_data={}))
    joined = "\n".join(captured_logs)
    assert f"process={PROCESS}" in next(line for line in captured_logs if "recorded" in line)
    assert "reason=wrong_key" in joined
    assert IIN not in joined
    assert DS_KEY not in joined


async def test_refused_body_leaves_its_reason_in_the_log(
    client: AsyncClient, captured_logs: list[str]
) -> None:
    await client.post(ROUTE, content=b"not json")
    await client.post(ROUTE, content=b"{}")
    joined = "\n".join(captured_logs)
    assert "reason=invalid_json" in joined
    assert "reason=no_process_id" in joined


async def test_callback_waiting_for_the_lock_ends_at_the_step_limit(
    connection: FakeConnection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def hang(*_: object) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(connection, "fetch", hang)
    monkeypatch.setattr(cron_run, "DB_STEP_SECONDS", 0.01)
    database = ClientSignDatabase(FakePool(connection))

    with pytest.raises(ClientSignDatabaseError):
        await asyncio.wait_for(
            database.step(lambda repository: repository.lock_sign_process("p")), 5
        )
