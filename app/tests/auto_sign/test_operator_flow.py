"""``cron-auto-sign-operator``: the whole flow and the exact requests."""

import json
from uuid import UUID

import pytest
from httpx import AsyncClient

from src.shared.advisory_lock import advisory_lock_key
from tests.auto_sign.fakes import (
    CLIENT_FILE_PATH,
    CLIENT_PROCESS,
    DN_NAME,
    FILE_BASE64,
    FIND_SIGN,
    GROUP,
    INSERT_SIGN,
    KEY_BASE64,
    KEY_INFO_PATH,
    KEY_PASSWORD,
    LOCK,
    MASTER_KEY,
    MODIFY_SIGN,
    MYNCA_TOKEN,
    MYNCA_URL,
    NEW_GROUP,
    NEW_PROCESS,
    NEW_SIGN_ID,
    OPERATOR_ROUTE,
    READ_KEY,
    READ_ROWS,
    SIGN_ID,
    SIGN_SAVE_PATH,
    Database,
    Mynca,
    assert_empty,
    sign_row,
)


async def test_whole_flow_runs_in_php_order_with_no_transaction_during_mynca(
    client: AsyncClient,
    db: Database,
    mynca: Mynca,
) -> None:
    response = await client.get(OPERATOR_ROUTE)

    assert_empty(response)
    assert db.events == [
        LOCK,
        "begin",
        READ_ROWS,
        "commit",
        "begin",
        INSERT_SIGN,
        "rollback",
        "begin",
        READ_KEY,
        "commit",
        f"mynca POST {KEY_INFO_PATH}",
        f"mynca GET {CLIENT_FILE_PATH}",
        f"mynca POST {SIGN_SAVE_PATH}",
        "begin",
        INSERT_SIGN,
        MODIFY_SIGN,
        "commit",
    ]
    assert mynca.transactions_during_calls == [0, 0, 0]
    assert db.committed_functions() == [INSERT_SIGN, MODIFY_SIGN]
    assert (db.released, db.lock_is_held) == (1, False)


async def test_the_key_is_read_with_the_master_key_and_given_as_base64(
    client: AsyncClient,
    db: Database,
    mynca: Mynca,
) -> None:
    await client.get(OPERATOR_ROUTE)

    assert db.calls_of(READ_KEY) == [("3", MASTER_KEY)]
    assert json.loads(mynca.request_to(KEY_INFO_PATH).content)["key"] == KEY_BASE64


async def test_exact_requests_to_mynca(client: AsyncClient, mynca: Mynca) -> None:
    await client.get(OPERATOR_ROUTE)

    info = mynca.request_to(KEY_INFO_PATH)
    assert info.method == "POST"
    assert str(info.url) == f"{MYNCA_URL}{KEY_INFO_PATH}"
    assert info.content.decode() == (
        f'{{"key":"{KEY_BASE64}","password":"{KEY_PASSWORD}","revocationCheck":["OCSP","CRL"]}}'
    )
    assert info.headers["content-type"] == "application/json"
    assert info.headers["accept"] == "application/json"
    assert info.headers["authorization"] == f"Bearer {MYNCA_TOKEN}"
    assert info.headers["user-agent"] == "smremont-mynca-client/1.0"

    client_file = mynca.request_to(CLIENT_FILE_PATH)
    assert client_file.method == "GET"
    assert client_file.content == b""
    assert client_file.headers["accept"] == "application/json"

    save = mynca.request_to(SIGN_SAVE_PATH)
    assert save.method == "POST"
    assert save.content.decode() == (
        f'{{"data":"{FILE_BASE64}",'
        f'"signer":{{"key":"{KEY_BASE64}","password":"{KEY_PASSWORD}"}},'
        f'"withTsp":true,"detached":false,"ext_id":501,"group_id":"{GROUP}"}}'
    )

    assert not any(path.endswith("/download-cms") for path in mynca.paths())


async def test_exact_database_calls(client: AsyncClient, db: Database) -> None:
    await client.get(OPERATOR_ROUTE)

    assert db.calls_of(INSERT_SIGN)[1:] == [
        (
            None,
            "501",
            None,
            "CONTRACT_SIGNER_DS_SIGN",
            "SIGN_CMS",
            "SIGN_CMS",
            None,
            DN_NAME,
            "MYNCA_AUTO",
            str(SIGN_ID),
        ),
    ]
    assert db.calls_of(MODIFY_SIGN) == [(str(NEW_SIGN_ID), NEW_PROCESS, NEW_GROUP, "CMS", None)]
    assert db.calls_of(FIND_SIGN) == []


async def test_parameters_go_as_text_for_the_server_to_convert(
    client: AsyncClient,
    db: Database,
) -> None:
    await client.get(OPERATOR_ROUTE)

    modify = next(query for query in db.route.queries if MODIFY_SIGN in query)
    assert "$1::text::integer, $2::text::uuid, $3::text::uuid" in modify


async def test_run_lock_key_is_fixed_and_differs_from_the_other_crons(
    client: AsyncClient,
    db: Database,
) -> None:
    await client.get(OPERATOR_ROUTE)

    key = advisory_lock_key("auto-sign:cron-auto-sign-operator")
    assert db.calls_of(LOCK) == [(key,)]
    assert key != advisory_lock_key("auto-sign:cron-auto-upload-sign-doc")


@pytest.mark.parametrize(
    ("changes", "ext_id", "insert_ids"),
    [
        ({"doc_type": "DS", "ds_id": 501}, 501, (None, "501", None)),
        (
            {"doc_type": "AGREEMENT", "ds_id": None, "agreement_client_request_id": 88},
            88,
            ("88", None, None),
        ),
        (
            {"doc_type": "APP_CLIENT", "ds_id": None, "client_request_document_id": 66},
            66,
            (None, None, "66"),
        ),
    ],
)
async def test_document_type_decides_ext_id_and_the_insert_columns(
    client: AsyncClient,
    db: Database,
    mynca: Mynca,
    changes: dict[str, object],
    ext_id: int,
    insert_ids: tuple[str | None, ...],
) -> None:
    db.responses[READ_ROWS] = [sign_row(**changes)]

    await client.get(OPERATOR_ROUTE)

    assert mynca.sign_save_bodies()[0]["ext_id"] == ext_id
    assert [args[:3] for args in db.calls_of(INSERT_SIGN)] == [insert_ids, insert_ids]


async def test_row_without_a_document_id_is_skipped_before_signing(
    client: AsyncClient,
    db: Database,
    mynca: Mynca,
    log_lines: list[str],
) -> None:
    db.responses[READ_ROWS] = [sign_row(ds_id=None)]

    assert_empty(await client.get(OPERATOR_ROUTE))

    assert mynca.requests == []
    assert db.calls_of(READ_KEY) == []
    assert db.calls_of(INSERT_SIGN) == []
    assert any(line.startswith("ERROR") and "no document id" in line for line in log_lines)


@pytest.mark.parametrize("group", [None, ""])
async def test_empty_group_of_the_row_is_no_group(
    client: AsyncClient,
    db: Database,
    mynca: Mynca,
    group: str | None,
) -> None:
    db.responses[READ_ROWS] = [sign_row(sign_group_id=group)]

    assert_empty(await client.get(OPERATOR_ROUTE))

    assert "group_id" not in mynca.sign_save_bodies()[0]


async def test_no_group_means_no_group_id_in_the_request(
    client: AsyncClient,
    db: Database,
    mynca: Mynca,
) -> None:
    db.responses[READ_ROWS] = [sign_row(sign_group_id=None)]

    await client.get(OPERATOR_ROUTE)

    assert "group_id" not in mynca.sign_save_bodies()[0]


async def test_group_of_the_answer_is_what_gets_recorded(
    client: AsyncClient,
    db: Database,
    mynca: Mynca,
) -> None:
    await client.get(OPERATOR_ROUTE)

    assert mynca.sign_save_bodies()[0]["group_id"] == GROUP
    assert db.calls_of(MODIFY_SIGN)[0][2] == NEW_GROUP


async def test_each_row_is_signed_once_in_list_order(
    client: AsyncClient,
    db: Database,
    mynca: Mynca,
) -> None:
    db.responses[READ_ROWS] = [sign_row(sign_id=1, ds_id=10), sign_row(sign_id=2, ds_id=20)]

    assert_empty(await client.get(OPERATOR_ROUTE))

    assert [body["ext_id"] for body in mynca.sign_save_bodies()] == [10, 20]
    assert [args[-1] for args in db.calls_of(INSERT_SIGN)] == ["1", "1", "2", "2"]


async def test_nothing_to_sign_does_not_touch_the_key_or_mynca(
    client: AsyncClient,
    db: Database,
    mynca: Mynca,
) -> None:
    db.responses[READ_ROWS] = []

    assert_empty(await client.get(OPERATOR_ROUTE))

    assert db.calls_of(READ_KEY) == []
    assert mynca.requests == []


async def test_process_id_is_read_from_the_uuid_asyncpg_gives(
    client: AsyncClient,
    db: Database,
    mynca: Mynca,
) -> None:
    db.responses[READ_ROWS] = [sign_row(sign_process_id=UUID(CLIENT_PROCESS))]

    await client.get(OPERATOR_ROUTE)

    assert CLIENT_FILE_PATH in mynca.paths()


async def test_dry_run_comes_before_the_key_and_always_rolls_back(
    client: AsyncClient,
    db: Database,
) -> None:
    await client.get(OPERATOR_ROUTE)

    dry_run = db.calls_of(INSERT_SIGN)[0]
    assert dry_run[7] == "IIN000000000000"
    assert db.events.index("rollback") < db.events.index(READ_KEY)
    assert db.committed_functions() == [INSERT_SIGN, MODIFY_SIGN]
    assert db.calls_of(INSERT_SIGN)[1][7] == DN_NAME


async def test_sign_save_leaves_a_trace_before_and_after(
    client: AsyncClient,
    log_lines: list[str],
) -> None:
    await client.get(OPERATOR_ROUTE)

    lines = "".join(log_lines)
    assert f"cms/sign-save starts | sign_id={SIGN_ID} ext_id=501" in lines
    assert f"cms/sign-save answered | sign_id={SIGN_ID} process={NEW_PROCESS}" in lines
    assert lines.index("cms/sign-save starts") < lines.index("cms/sign-save answered")


async def test_cron_answer_has_the_session_headers_of_php(client: AsyncClient) -> None:
    """Found by the contour comparison: PHP opens a session, and its headers come with the answer."""
    response = await client.get(OPERATOR_ROUTE)

    assert response.content == b""
    assert response.headers["cache-control"] == "no-store, no-cache, must-revalidate"
    assert response.headers["pragma"] == "no-cache"
