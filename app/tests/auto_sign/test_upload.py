"""``cron-auto-upload-sign-doc``: the signed PDF goes from MyNCA to storage and into the request."""

import asyncio
import re

import httpx
import pytest
from asyncpg.exceptions import RaiseError
from httpx import AsyncClient

from src.features.auto_sign import document_upload
from tests.auto_sign.conftest import Storage
from tests.auto_sign.fakes import (
    GROUP,
    GROUP_PDF_PATH,
    LOCK,
    MYNCA_TOKEN,
    MYNCA_URL,
    PDF_BYTES,
    READ_DOCS,
    READ_GROUP,
    UPDATE_DOC,
    UPLOAD_ROUTE,
    Database,
    Mynca,
    assert_empty,
)

pytestmark = pytest.mark.usefixtures("storage")

DOCUMENT_PATH = re.compile(
    r"^/documents/\d{4}\.\d{2}\.\d{2}/client_request_docs/cr_doc[0-9a-f]{13}\.\d{8}\.pdf$",
)


async def test_whole_flow_in_php_order(
    client: AsyncClient,
    db: Database,
    mynca: Mynca,
    storage: Storage,
) -> None:
    assert_empty(await client.get(UPLOAD_ROUTE))

    assert db.events == [
        LOCK,
        "begin",
        READ_DOCS,
        "commit",
        "begin",
        READ_GROUP,
        "commit",
        f"mynca GET {GROUP_PDF_PATH}",
        "begin",
        UPDATE_DOC,
        "commit",
    ]
    assert mynca.transactions_during_calls == [0]
    assert (db.released, db.lock_is_held) == (1, False)
    assert db.calls_of(READ_GROUP) == [("88",)]


async def test_exact_request_to_mynca(client: AsyncClient, mynca: Mynca) -> None:
    await client.get(UPLOAD_ROUTE)

    request = mynca.request_to(GROUP_PDF_PATH)
    assert request.method == "GET"
    assert str(request.url) == f"{MYNCA_URL}/sign/group/{GROUP}/download"
    assert request.content == b""
    assert request.headers["accept"] == "*/*"
    assert request.headers["authorization"] == f"Bearer {MYNCA_TOKEN}"


async def test_file_goes_to_storage_under_the_path_php_builds(
    client: AsyncClient,
    db: Database,
    storage: Storage,
) -> None:
    await client.get(UPLOAD_ROUTE)

    assert len(storage.puts) == 1
    put = storage.puts[0]
    assert put["body"] == PDF_BYTES
    assert put["content_type"] == "application/pdf"
    args = db.calls_of(UPDATE_DOC)[0]
    assert DOCUMENT_PATH.fullmatch(args[2])
    assert put["key"] == args[2].removeprefix("/")


@pytest.mark.parametrize(
    ("sign_cnt", "create_task", "update"),
    [
        (1, "true", "false"),
        (2, "false", "true"),
        (3, "false", "false"),
        (0, "false", "false"),
    ],
)
async def test_sign_count_decides_the_flags(
    client: AsyncClient,
    db: Database,
    sign_cnt: int,
    create_task: str,
    update: str,
) -> None:
    db.responses[READ_DOCS] = [{"agreement_client_request_id": 88, "sign_cnt": sign_cnt}]

    await client.get(UPLOAD_ROUTE)

    args = db.calls_of(UPDATE_DOC)[0]
    assert args[0] == "88"
    assert args[1] == "Договор_оказания_услуг_88.pdf"
    assert args[3:] == (create_task, update)


async def test_nothing_to_upload(client: AsyncClient, db: Database, mynca: Mynca) -> None:
    db.responses[READ_DOCS] = []

    assert_empty(await client.get(UPLOAD_ROUTE))

    assert mynca.requests == []


@pytest.mark.parametrize("group_answer", [[], [{"sign_group_id": None}], RaiseError("{broken}")])
async def test_no_group_is_logged_and_the_loop_goes_on(
    client: AsyncClient,
    db: Database,
    mynca: Mynca,
    log_lines: list[str],
    group_answer: object,
) -> None:
    db.responses[READ_DOCS] = [
        {"agreement_client_request_id": 88, "sign_cnt": 1},
        {"agreement_client_request_id": 89, "sign_cnt": 1},
    ]
    db.responses[READ_GROUP] = group_answer

    assert_empty(await client.get(UPLOAD_ROUTE))

    assert mynca.requests == []
    assert db.calls_of(UPDATE_DOC) == []
    assert len(db.calls_of(READ_GROUP)) == 2
    assert sum(line.startswith("ERROR") for line in log_lines) == 2


@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(500, content=b"%PDF"),
        httpx.Response(200, content=b""),
        httpx.Response(200, content=b"0"),
        httpx.ReadTimeout("slow"),
    ],
)
async def test_no_file_from_mynca_writes_nothing(
    client: AsyncClient,
    db: Database,
    mynca: Mynca,
    storage: Storage,
    answer: object,
) -> None:
    mynca.answers[GROUP_PDF_PATH] = answer

    assert_empty(await client.get(UPLOAD_ROUTE))

    assert storage.puts == []
    assert db.calls_of(UPDATE_DOC) == []


async def test_storage_failure_writes_nothing_and_the_loop_goes_on(
    client: AsyncClient,
    db: Database,
    mynca: Mynca,
    storage: Storage,
) -> None:
    db.responses[READ_DOCS] = [
        {"agreement_client_request_id": 88, "sign_cnt": 1},
        {"agreement_client_request_id": 89, "sign_cnt": 1},
    ]
    storage.error = OSError("minio down")

    assert_empty(await client.get(UPLOAD_ROUTE))

    assert db.calls_of(UPDATE_DOC) == []
    assert mynca.paths().count(GROUP_PDF_PATH) == 2


async def test_hung_storage_is_a_failed_item(
    client: AsyncClient,
    db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def hang(**_: object) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(document_upload, "put_object", hang)
    monkeypatch.setattr(document_upload, "_STORE_SECONDS", 0.01)

    assert_empty(await client.get(UPLOAD_ROUTE))

    assert db.calls_of(UPDATE_DOC) == []
    assert db.released == 1


async def test_failed_update_is_logged_and_the_loop_goes_on(
    client: AsyncClient,
    db: Database,
    log_lines: list[str],
) -> None:
    db.responses[READ_DOCS] = [
        {"agreement_client_request_id": 88, "sign_cnt": 2},
        {"agreement_client_request_id": 89, "sign_cnt": 2},
    ]
    db.responses[UPDATE_DOC] = RaiseError("{broken}")

    assert_empty(await client.get(UPLOAD_ROUTE))

    assert len(db.calls_of(UPDATE_DOC)) == 2
    assert db.committed == []
    assert sum(line.startswith("ERROR") for line in log_lines) == 2


async def test_read_failure_ends_quietly(client: AsyncClient, db: Database, mynca: Mynca) -> None:
    db.responses[READ_DOCS] = RaiseError("{broken}")

    assert_empty(await client.get(UPLOAD_ROUTE))

    assert mynca.requests == []


async def test_overlapping_run_touches_nothing(
    client: AsyncClient,
    db: Database,
    mynca: Mynca,
) -> None:
    db.lock_is_held = True

    assert_empty(await client.get(UPLOAD_ROUTE))

    assert db.calls_of(READ_DOCS) == []
    assert mynca.requests == []


@pytest.mark.usefixtures("short_limits")
@pytest.mark.parametrize("function", [READ_DOCS, READ_GROUP, UPDATE_DOC])
async def test_hung_step_does_not_stop_the_cron(
    client: AsyncClient,
    db: Database,
    function: str,
) -> None:
    db.hung_functions = {function: 1}

    assert_empty(await client.get(UPLOAD_ROUTE))

    assert (db.released, db.lock_is_held) == (1, False)
    assert db.open_transactions == 0
