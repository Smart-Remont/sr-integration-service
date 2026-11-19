"""``cron-auto-sign-operator``: each failure before and after the signature, and its rollback."""

import httpx
import pytest
from asyncpg.exceptions import RaiseError
from httpx import AsyncClient

from tests.auto_sign.fakes import (
    CLIENT_FILE_PATH,
    INSERT_SIGN,
    KEY_INFO_PATH,
    MODIFY_SIGN,
    NEW_GROUP,
    NEW_PROCESS,
    NEW_SIGN_ID,
    OPERATOR_ROUTE,
    READ_KEY,
    READ_ROWS,
    ROLLBACK_PATH,
    SIGN_SAVE_PATH,
    Database,
    Mynca,
    assert_empty,
    fail_after_dry_run,
    sign_row,
)


def assert_no_signature(db: Database, mynca: Mynca) -> None:
    assert SIGN_SAVE_PATH not in mynca.paths()
    assert ROLLBACK_PATH not in mynca.paths()
    assert db.committed == []


def assert_rolled_back_and_nothing_kept(db: Database, mynca: Mynca) -> None:
    assert mynca.paths().count(ROLLBACK_PATH) == 1
    assert mynca.paths()[-1] == ROLLBACK_PATH
    assert db.committed == []


@pytest.mark.parametrize("company_id", [None, 0, -1])
async def test_no_company_skips_the_item_before_the_key(
    client: AsyncClient,
    db: Database,
    mynca: Mynca,
    company_id: int | None,
) -> None:
    db.responses[READ_ROWS] = [sign_row(company_id=company_id)]

    assert_empty(await client.get(OPERATOR_ROUTE))

    assert db.calls_of(READ_KEY) == []
    assert mynca.requests == []


@pytest.mark.parametrize("doc_type", ["DS", "AGREEMENT", "APP_CLIENT"])
async def test_row_without_a_client_process_is_skipped_with_a_logged_error(
    client: AsyncClient,
    db: Database,
    mynca: Mynca,
    log_lines: list[str],
    doc_type: str,
) -> None:
    db.responses[READ_ROWS] = [sign_row(sign_process_id=None, doc_type=doc_type)]

    assert_empty(await client.get(OPERATOR_ROUTE))

    assert db.calls_of(READ_KEY) == []
    assert mynca.requests == []
    assert any("ERROR" in line and "legacy branch" in line for line in log_lines)


@pytest.mark.parametrize(
    "key_answer",
    [
        RaiseError("{Активный ключ для компании 3 не найден}"),
        [],
        [{"key_data": "\\x00ff", "key_password": "x"}],
        [{"key_data": b"\x00", "key_password": None}],
    ],
)
async def test_no_usable_key_skips_the_item(
    client: AsyncClient,
    db: Database,
    mynca: Mynca,
    key_answer: object,
) -> None:
    db.responses[READ_KEY] = key_answer

    assert_empty(await client.get(OPERATOR_ROUTE))

    assert mynca.requests == []
    assert db.committed == []


@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(200, json={"valid": False}),
        httpx.Response(200, json={"valid": "true"}),
        httpx.Response(200, json={}),
        httpx.Response(500, json={"valid": True}),
        httpx.ConnectTimeout("no route"),
    ],
)
async def test_invalid_certificate_stops_before_the_file_and_the_signature(
    client: AsyncClient,
    db: Database,
    mynca: Mynca,
    answer: object,
) -> None:
    mynca.answers[KEY_INFO_PATH] = answer

    assert_empty(await client.get(OPERATOR_ROUTE))

    assert mynca.paths() == [KEY_INFO_PATH]
    assert_no_signature(db, mynca)


@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(404, json={"data": {"file_content": "x"}}),
        httpx.Response(200, json={"data": {}}),
        httpx.Response(200, json={"data": {"file_content": None}}),
        httpx.Response(200, json={"data": {"file_content": ""}}),
        httpx.Response(200, json={"data": "text"}),
        httpx.Response(200, text="not json"),
        httpx.ReadTimeout("slow"),
    ],
)
async def test_no_client_file_means_no_signature(
    client: AsyncClient,
    db: Database,
    mynca: Mynca,
    answer: object,
) -> None:
    mynca.answers[CLIENT_FILE_PATH] = answer

    assert_empty(await client.get(OPERATOR_ROUTE))

    assert_no_signature(db, mynca)


async def test_clear_refusal_of_sign_save_goes_on_to_the_next_document(
    client: AsyncClient,
    db: Database,
    mynca: Mynca,
) -> None:
    db.responses[READ_ROWS] = [sign_row(sign_id=1), sign_row(sign_id=2)]
    mynca.answers[SIGN_SAVE_PATH] = httpx.Response(400, json={"error": "bad"})

    assert_empty(await client.get(OPERATOR_ROUTE))

    assert mynca.paths().count(SIGN_SAVE_PATH) == 2
    assert ROLLBACK_PATH not in mynca.paths()
    assert db.committed == []


@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(504, text="gateway"),
        httpx.ReadTimeout("slow"),
        ValueError("unexpected"),
        httpx.Response(200, json={}),
        httpx.Response(200, json={"dn_name": "x"}),
        httpx.Response(200, json={"sign_process_id": "not-a-uuid"}),
        httpx.Response(200, json={"sign_process_id": ""}),
    ],
)
async def test_first_unclear_answer_ends_the_run(
    client: AsyncClient,
    db: Database,
    mynca: Mynca,
    log_lines: list[str],
    answer: object,
) -> None:
    db.responses[READ_ROWS] = [sign_row(sign_id=1), sign_row(sign_id=2)]
    mynca.answers[SIGN_SAVE_PATH] = answer

    assert_empty(await client.get(OPERATOR_ROUTE))

    assert mynca.paths().count(SIGN_SAVE_PATH) == 1
    assert ROLLBACK_PATH not in mynca.paths()
    assert db.committed == []
    assert any(line.startswith("CRITICAL") and "sign_id=1" in line for line in log_lines)


@pytest.mark.parametrize("group", ["not-a-uuid", "123"])
async def test_row_with_a_bad_uuid_is_not_signed(
    client: AsyncClient,
    db: Database,
    mynca: Mynca,
    group: str,
) -> None:
    db.responses[READ_ROWS] = [sign_row(sign_group_id=group)]

    assert_empty(await client.get(OPERATOR_ROUTE))

    assert mynca.requests == []
    assert db.calls_of(INSERT_SIGN) == []


async def test_answer_with_a_bad_group_is_rolled_back_without_a_write(
    client: AsyncClient,
    db: Database,
    mynca: Mynca,
) -> None:
    mynca.answers[SIGN_SAVE_PATH] = httpx.Response(
        200,
        json={"sign_process_id": NEW_PROCESS, "dn_name": "x", "group_id": "not-a-uuid"},
    )

    assert_empty(await client.get(OPERATOR_ROUTE))

    assert len(db.calls_of(INSERT_SIGN)) == 1
    assert_rolled_back_and_nothing_kept(db, mynca)


async def test_empty_group_of_the_answer_is_recorded_as_null(
    client: AsyncClient,
    db: Database,
    mynca: Mynca,
) -> None:
    mynca.answers[SIGN_SAVE_PATH] = httpx.Response(
        200,
        json={"sign_process_id": NEW_PROCESS, "dn_name": "x", "group_id": ""},
    )

    assert_empty(await client.get(OPERATOR_ROUTE))

    assert db.calls_of(MODIFY_SIGN)[0][2] is None
    assert NEW_GROUP not in str(db.calls_of(MODIFY_SIGN))


async def test_dry_run_refusal_means_no_key_and_no_signature(
    client: AsyncClient,
    db: Database,
    mynca: Mynca,
    log_lines: list[str],
) -> None:
    db.responses[READ_ROWS] = [sign_row(sign_id=1), sign_row(sign_id=2)]
    db.responses[INSERT_SIGN] = RaiseError("{Клиентский ДС не подписан клиентом}")

    assert_empty(await client.get(OPERATOR_ROUTE))

    assert db.calls_of(READ_KEY) == []
    assert mynca.requests == []
    assert db.committed == []
    assert sum("would be refused" in line for line in log_lines) == 2


async def test_failed_insert_rolls_the_signature_back(
    client: AsyncClient,
    db: Database,
    mynca: Mynca,
) -> None:
    db.responses[INSERT_SIGN] = fail_after_dry_run(RaiseError("{ИИН не соответствует}"))

    assert_empty(await client.get(OPERATOR_ROUTE))

    assert db.calls_of(MODIFY_SIGN) == []
    assert_rolled_back_and_nothing_kept(db, mynca)


async def test_failed_modify_drops_the_insert_too_so_the_next_run_can_sign_again(
    client: AsyncClient,
    db: Database,
    mynca: Mynca,
) -> None:
    db.responses[MODIFY_SIGN] = RaiseError("{broken}")

    assert_empty(await client.get(OPERATOR_ROUTE))

    assert len(db.calls_of(INSERT_SIGN)) == 2
    assert_rolled_back_and_nothing_kept(db, mynca)


@pytest.mark.parametrize(
    "answer",
    [httpx.Response(500, json={"error": "no"}), httpx.ReadTimeout("slow")],
)
async def test_failed_rollback_is_a_critical_line_and_the_loop_goes_on(
    client: AsyncClient,
    db: Database,
    mynca: Mynca,
    log_lines: list[str],
    answer: object,
) -> None:
    db.responses[READ_ROWS] = [sign_row(sign_id=1), sign_row(sign_id=2)]
    db.responses[INSERT_SIGN] = fail_after_dry_run(RaiseError("{broken}"))
    mynca.answers[ROLLBACK_PATH] = answer

    assert_empty(await client.get(OPERATOR_ROUTE))

    assert mynca.paths().count(ROLLBACK_PATH) == 2
    critical = [line for line in log_lines if line.startswith("CRITICAL")]
    assert len(critical) == 2
    assert all("ROLLBACK FAILED" in line and NEW_PROCESS in line for line in critical)


async def test_one_failed_item_does_not_stop_the_next(
    client: AsyncClient,
    db: Database,
    mynca: Mynca,
) -> None:
    db.responses[READ_ROWS] = [sign_row(sign_id=1), sign_row(sign_id=2)]
    answers = iter(
        [
            [{"insert_sign_general": NEW_SIGN_ID}],
            RaiseError("{first}"),
            [{"insert_sign_general": NEW_SIGN_ID}],
            [{"insert_sign_general": NEW_SIGN_ID}],
        ],
    )

    def insert(_: tuple[object, ...]) -> list[dict[str, int]]:
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        return answer

    db.responses[INSERT_SIGN] = insert

    assert_empty(await client.get(OPERATOR_ROUTE))

    assert mynca.paths().count(ROLLBACK_PATH) == 1
    assert db.committed_functions() == [INSERT_SIGN, MODIFY_SIGN]


async def test_unexpected_error_of_the_cron_read_ends_quietly(
    client: AsyncClient,
    db: Database,
    mynca: Mynca,
) -> None:
    db.responses[READ_ROWS] = RaiseError("{Не удалось определить company_id для sign_id=1}")

    assert_empty(await client.get(OPERATOR_ROUTE))

    assert mynca.requests == []
    assert db.released == 1
