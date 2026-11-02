"""``rest/buh`` moved from PHP: the paths of ``what`` that are alive. No real 1C, no real database."""

import json
from collections.abc import Iterator

import pytest
from asyncpg.exceptions import (
    AdminShutdownError,
    ConnectionDoesNotExistError,
    DivisionByZeroError,
    ObjectNotInPrerequisiteStateError,
    RaiseError,
)
from httpx import AsyncClient
from loguru import logger

from src.exceptions.infra import UnexpectedDatabaseError
from src.features.buh_1c.errors import ACTION_CONTENT_TYPE, Buh1cFunctionError
from src.features.buh_1c.repo import Buh1cDatabase
from src.features.buh_1c.service import Buh1cService
from tests.buh_1c.conftest import (
    CHECK,
    JOURNAL,
    LOT_READ,
    LOT_UPD,
    MATERIAL_UPD,
    ROUTE,
    WAREHOUSE_UPD,
    Buh1cConnection,
    FakePool,
)

CACHE_CONTROL = "no-cache, must-revalidate, max-age=0"
UPDATES = [
    ("lot_guid", LOT_UPD),
    ("material_guid", MATERIAL_UPD),
    ("warehouse_guid", WAREHOUSE_UPD),
]
BODY = '{"warehouse":[{"warehouse_id":4,"warehouse_guid":"g/я"}]}'


def _raised(text: str) -> RaiseError:
    error = RaiseError(text)
    error.sqlstate = "P0001"
    return error


def _with_state[T: Exception](error: T, state: str) -> T:
    error.sqlstate = state
    return error


def _pdo_debug(journal_row: str) -> str:
    return json.loads(journal_row)["error_debug"]


async def test_lot_reads_then_writes_the_value_to_the_journal(
    client: AsyncClient, db: Buh1cConnection
) -> None:
    response = await client.get(ROUTE, params={"what": "lot"})

    assert response.status_code == 200
    assert response.content == b'{"value": [], "status": true}'
    assert response.headers["content-type"] == "application/json"
    assert response.headers["cache-control"] == CACHE_CONTROL
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["expires"] == "Thu, 19 Nov 1981 08:52:00 GMT"
    assert db.calls == [
        (CHECK, ()),
        (LOT_READ, ()),
        (JOURNAL, ('{"value": [], "status": true}', "lot")),
    ]


@pytest.mark.parametrize(("what", "function"), UPDATES)
async def test_update_writes_the_journal_first_and_returns_the_value(
    client: AsyncClient, db: Buh1cConnection, what: str, function: str
) -> None:
    db.responses[function] = [{"id": '{"a": "b/я"}'}]

    response = await client.post(ROUTE, params={"what": what}, content=BODY.encode())

    assert response.status_code == 200
    assert response.content == '{"a": "b/я"}'.encode()
    assert response.headers["content-type"] == "application/json"
    assert db.calls == [(CHECK, ()), (JOURNAL, (BODY, what)), (function, (BODY,))]


async def test_each_function_has_its_own_transaction_as_in_php(
    client: AsyncClient, db: Buh1cConnection, pool: FakePool
) -> None:
    await client.post(ROUTE, params={"what": "warehouse_guid"}, content=BODY.encode())

    assert db.transactions_opened == 3
    assert db.transactions_closed == ["commit", "commit", "commit"]
    assert pool.acquired == 3


async def test_every_step_runs_on_custom_plans_as_in_a_new_php_session(
    client: AsyncClient, db: Buh1cConnection
) -> None:
    await client.get(ROUTE, params={"what": "lot"})

    plans = [query for query in db.config_queries if "plan_cache_mode" in query]
    assert len(plans) == 3


@pytest.mark.parametrize(("what", "function"), UPDATES)
async def test_failed_update_keeps_the_journal_and_answers_400(
    client: AsyncClient, db: Buh1cConnection, what: str, function: str
) -> None:
    db.responses[function] = _raised("{Отказ a/b}")

    response = await client.post(ROUTE, params={"what": what}, content=BODY.encode())

    assert response.status_code == 400
    assert response.content == '{\n    "error": "Отказ a/b"\n}'.encode()
    assert response.headers["content-type"] == "application/json"
    assert response.headers["cache-control"] == CACHE_CONTROL
    assert db.transactions_closed == ["commit", "commit", "rollback"]


async def test_failure_without_a_raised_text_gets_the_php_prefix(
    client: AsyncClient, db: Buh1cConnection
) -> None:
    error = DivisionByZeroError("division by zero")
    error.sqlstate = "22012"
    db.responses[WAREHOUSE_UPD] = error

    response = await client.post(ROUTE, params={"what": "warehouse_guid"}, content=b"{}")

    assert response.status_code == 400
    assert json.loads(response.content) == {
        "error": "Произошла необработанная ошибка в БД "
        "SQLSTATE[22012]: Division by zero: 7 ERROR:  division by zero"
    }


async def test_failed_lot_read_writes_null_to_the_journal(
    client: AsyncClient, db: Buh1cConnection
) -> None:
    db.responses[LOT_READ] = _raised("{Отказ}")

    response = await client.get(ROUTE, params={"what": "lot"})

    assert response.status_code == 400
    assert response.content == '{\n    "error": "Отказ"\n}'.encode()
    assert db.calls[-1] == (JOURNAL, (None, "lot"))


async def test_failed_journal_changes_nothing_for_the_caller(
    client: AsyncClient, db: Buh1cConnection
) -> None:
    db.responses[JOURNAL] = _raised("{Журнал}")

    read = await client.get(ROUTE, params={"what": "lot"})
    update = await client.post(ROUTE, params={"what": "warehouse_guid"}, content=BODY.encode())

    assert read.status_code == 200
    assert read.content == b'{"value": [], "status": true}'
    assert update.status_code == 200
    assert update.content == b'{"warehouse": []}'
    assert (WAREHOUSE_UPD, (BODY,)) in db.calls


@pytest.mark.parametrize(
    ("value", "body"),
    [(None, b""), ("", b""), ('{"a":"/"}', b'{"a":"/"}')],
)
async def test_function_value_goes_out_as_it_is(
    client: AsyncClient, db: Buh1cConnection, value: str | None, body: bytes
) -> None:
    db.responses[LOT_READ] = [{"id": value}]

    response = await client.get(ROUTE, params={"what": "lot"})

    assert response.status_code == 200
    assert response.content == body
    assert response.headers["content-type"] == "application/json"
    assert db.calls[-1] == (JOURNAL, (value, "lot"))


@pytest.mark.parametrize(
    "what",
    ["no_such", "LOT", "lot ", " lot", "0", "лот"],
)
async def test_what_php_has_no_branch_for_is_an_empty_200_after_the_check(
    client: AsyncClient, db: Buh1cConnection, what: str
) -> None:
    response = await client.post(ROUTE, params={"what": what}, content=BODY.encode())

    assert response.status_code == 200
    assert response.content == b""
    assert response.headers["content-type"] == ACTION_CONTENT_TYPE
    assert response.headers["cache-control"] == CACHE_CONTROL
    assert db.functions() == [CHECK]


async def test_no_what_at_all_is_an_empty_200(client: AsyncClient, db: Buh1cConnection) -> None:
    response = await client.get(ROUTE)

    assert response.status_code == 200
    assert response.content == b""
    assert db.functions() == [CHECK]


@pytest.mark.parametrize("params", [{"what[]": "lot"}, {"what[a]": "lot"}])
async def test_what_as_an_array_matches_no_branch(
    client: AsyncClient, db: Buh1cConnection, params: dict[str, str]
) -> None:
    response = await client.get(ROUTE, params=params)

    assert response.status_code == 200
    assert response.content == b""
    assert db.functions() == [CHECK]


async def test_failed_check_answers_400_and_writes_the_php_result_to_the_journal(
    client: AsyncClient, db: Buh1cConnection
) -> None:
    db.responses[CHECK] = _raised("{Отказ a/b}")

    response = await client.post(ROUTE, params={"what": "warehouse_guid"}, content=BODY.encode())

    assert response.status_code == 400
    assert response.content == '{\n    "error": "Отказ a/b"\n}'.encode()
    assert db.functions() == [CHECK, JOURNAL]
    function, (journal_row, what) = db.calls[1]
    assert function == JOURNAL
    assert what == "warehouse_guid"
    assert journal_row.startswith('{"value":null,"error":"\\u041e\\u0442')
    assert "a\\/b" in journal_row
    assert journal_row.endswith('"status":false}')
    assert list(json.loads(journal_row)) == ["value", "error", "error_debug", "status"]


@pytest.mark.parametrize("what", ["lot", "no_such", ""])
async def test_failed_check_stops_every_branch(
    client: AsyncClient, db: Buh1cConnection, what: str
) -> None:
    db.responses[CHECK] = _raised("{Отказ}")

    response = await client.get(ROUTE, params={"what": what})

    assert response.status_code == 400
    assert db.functions() == [CHECK, JOURNAL]


async def test_failed_check_journals_an_array_what_as_the_word_php_gives_it(
    client: AsyncClient, db: Buh1cConnection
) -> None:
    db.responses[CHECK] = _raised("{Отказ}")

    await client.get(ROUTE, params={"what[]": "lot"})

    assert db.calls[1][1][1] == "Array"


async def test_body_with_nul_ends_at_it_as_in_libpq(
    client: AsyncClient, db: Buh1cConnection
) -> None:
    await client.post(ROUTE, params={"what": "lot_guid"}, content=b'{"lot":[]}\x00tail')

    assert db.calls[1:] == [
        (JOURNAL, ('{"lot":[]}', "lot_guid")),
        (LOT_UPD, ('{"lot":[]}',)),
    ]


async def test_body_with_invalid_utf8_is_refused_by_the_check_pdo_made(
    client: AsyncClient, db: Buh1cConnection
) -> None:
    response = await client.post(ROUTE, params={"what": "lot_guid"}, content=b'{"a":"\xff"}')

    assert response.status_code == 400
    assert json.loads(response.content) == {
        "error": "Произошла необработанная ошибка в БД SQLSTATE[22021]: Character not in "
        'repertoire: 7 ERROR:  invalid byte sequence for encoding "UTF8": 0xff'
    }
    # Neither call reached the database: PDO refused both, and PHP ignored the journal's refusal.
    assert db.functions() == [CHECK]


async def test_multipart_body_is_lost_as_in_php(client: AsyncClient, db: Buh1cConnection) -> None:
    await client.post(
        ROUTE,
        params={"what": "warehouse_guid"},
        content=b"--b\r\n\r\nx\r\n--b--\r\n",
        headers={"Content-Type": "multipart/form-data; boundary=b"},
    )

    assert db.calls[1:] == [(JOURNAL, ("", "warehouse_guid")), (WAREHOUSE_UPD, ("",))]


@pytest.mark.parametrize(
    ("path", "params", "data"),
    [
        ("/what/lot", {}, None),
        ("/what/lot/", {}, None),
        ("", {"what": "lot"}, None),
        ("/", {"what": "lot"}, None),
        ("/what/lot", {"what": "no_such"}, None),
        ("", {"what": "lot"}, {"what": "no_such"}),
        ("", {}, {"what": "lot"}),
    ],
)
async def test_what_is_found_where_zend_finds_it(
    client: AsyncClient,
    db: Buh1cConnection,
    path: str,
    params: dict[str, str],
    data: dict[str, str] | None,
) -> None:
    response = await client.post(f"{ROUTE}{path}", params=params, data=data)

    assert response.status_code == 200
    assert LOT_READ in db.functions()


@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "DELETE", "PATCH"])
async def test_any_method_reaches_the_branch(
    client: AsyncClient, db: Buh1cConnection, method: str
) -> None:
    response = await client.request(method, ROUTE, params={"what": "lot"})

    assert response.status_code == 200
    assert LOT_READ in db.functions()


@pytest.mark.parametrize(
    "error",
    [
        _with_state(AdminShutdownError("terminating connection"), "57P01"),
        _with_state(ConnectionDoesNotExistError("closed in the middle of operation"), "08003"),
        OSError("no route"),
    ],
)
@pytest.mark.parametrize("function", [CHECK, JOURNAL])
async def test_no_database_is_not_a_refusal_of_a_function(
    db: Buh1cConnection, pool: FakePool, error: Exception, function: str
) -> None:
    db.responses[function] = error

    with pytest.raises((type(error), UnexpectedDatabaseError)):
        await Buh1cService(Buh1cDatabase(pool)).exchange(what="lot", raw_body=b"")


async def test_database_that_refuses_the_connection_is_not_a_refusal_of_a_function() -> None:
    class Refusing:
        def acquire(self) -> Refusing:
            return self

        async def __aenter__(self) -> None:
            raise ObjectNotInPrerequisiteStateError("database is not accepting connections")

        async def __aexit__(self, *_: object) -> None:
            return None

    with pytest.raises(ObjectNotInPrerequisiteStateError):
        await Buh1cService(Buh1cDatabase(Refusing())).exchange(what="lot", raw_body=b"")


@pytest.fixture
def log_lines() -> Iterator[list[str]]:
    lines: list[str] = []
    sink = logger.add(lambda message: lines.append(str(message)), format="{level} {message}")
    yield lines
    logger.remove(sink)


async def test_failure_of_the_database_is_an_error_in_the_log_without_its_text(
    client: AsyncClient, db: Buh1cConnection, log_lines: list[str]
) -> None:
    error = _with_state(DivisionByZeroError("секрет из тела"), "22012")
    db.responses[WAREHOUSE_UPD] = error

    await client.post(ROUTE, params={"what": "warehouse_guid"}, content=b"{}")

    assert log_lines == ["ERROR rest/buh: warehouse_guid refused, state 22012\n"]


@pytest.mark.parametrize(
    ("function", "what", "step"),
    [
        (CHECK, "lot", "rest.get_buh_url"),
        (LOT_READ, "lot", "lot"),
        (LOT_UPD, "lot_guid", "lot_guid"),
    ],
)
async def test_refusal_of_a_function_is_info_in_the_log(
    client: AsyncClient,
    db: Buh1cConnection,
    log_lines: list[str],
    function: str,
    what: str,
    step: str,
) -> None:
    db.responses[function] = _raised("{Отказ}")

    await client.post(ROUTE, params={"what": what}, content=b"{}")

    assert log_lines == [f"INFO rest/buh: {step} refused, state P0001\n"]


async def test_failed_check_does_not_put_the_what_of_the_request_in_the_log(
    client: AsyncClient, db: Buh1cConnection, log_lines: list[str]
) -> None:
    db.responses[CHECK] = _raised("{Отказ}")

    await client.get(ROUTE, params={"what": "секрет"})

    assert not any("секрет" in line for line in log_lines)


def test_function_error_keeps_the_php_order_of_the_result() -> None:
    error = Buh1cFunctionError("SQLSTATE[P0001]: Raise exception: 7 ERROR:  {Я/б}", state="P0001")

    assert error.text == "Я/б"
    assert _pdo_debug(error.php_result_json()) == error.pdo_message
    assert "Я" not in error.php_result_json()
