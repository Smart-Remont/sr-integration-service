"""``kaspi-request-pay`` moved from PHP ``ClientController``. Expected bytes were checked against
the PHP stand for check and errors; the pay answers follow the rows PHP left in
``kaspi_pay_log_tab``. The pay command is never run on the stand, so it is covered only here.
"""

import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from asyncpg.exceptions import (
    InvalidDatetimeFormatError,
    InvalidTextRepresentationError,
    LockNotAvailableError,
    RaiseError,
    UniqueViolationError,
)
from httpx import ASGITransport, AsyncClient

from src import repository
from src.config import kaspi_proxy_config
from src.database.deps import get_db_connection
from src.features.kaspi.repo import account_lock_key
from src.main import app
from tests.big_integration.test_contract import FakeConnection

REQUEST_ROUTE = "/api/kaspi/kaspi-request-pay"
PROXY_SECRET = "test-kaspi-proxy-secret"
PROXY_HEADERS = {"X-Kaspi-Proxy-Secret": PROXY_SECRET}
XML_TYPE = "text/xml;charset=UTF-8"
HTML_TYPE = "text/html; charset=UTF-8"
DECLARATION = b'<?xml version="1.0" encoding="UTF-8"?>\n'
CHECK = "client.kaspi_check"
REQUEST_INFO = "client.kaspi_request_get_info"
REQUEST_PAY = "client.kaspi_request_pay"
AFTER = "public.after_payment_operations"
LOG = "client.insert_kaspi_pay_log"
LOCK = "pg_catalog.pg_advisory_xact_lock"
WRITES = {REQUEST_PAY, AFTER, LOG}

REQUEST_INFO_ROW = {
    "prop_fio": "Иванов Иван",
    "prop_number": "12345",
    "prop_date": "01.02.2026",
    "resident_name": "ЖК Тест",
    "company_bin": "123456789012",
    "company_name_official": "ТОО «Компания»",
}
FIELDS = (
    "  <fields>\n"
    '    <field1 name="ФИО">Иванов Иван</field1>\n'
    '    <field2 name="Номер договора">12345</field2>\n'
    '    <field3 name="Дата договора">01.02.2026</field3>\n'
    '    <field4 name="ЖК">ЖК Тест</field4>\n'
    '    <field5 name="Название компании">ТОО «Компания»</field5>\n'
    "  </fields>\n"
)
REQUEST_CHECK_XML = (
    DECLARATION.decode()
    + "<response>\n  <txn_id>T1</txn_id>\n  <result>0</result>\n  <bin>123456789012</bin>\n"
    + FIELDS
    + "  <comment/>\n</response>\n"
).encode()
ACCEPTED = {"account": "77", "iin": "990101300000", "sum": "150000", "txn_id": "T1"}
PAY = {**ACCEPTED, "command": "pay", "txn_date": "20261102120000"}
CHECK_QUERY = {**ACCEPTED, "command": "check"}


def pay_xml(provider_txn: int, *, company_bin: str = "<bin>123456789012</bin>") -> bytes:
    return (
        DECLARATION.decode() + "<response>\n  <txn_id>T1</txn_id>\n"
        f"  <prv_txn>{provider_txn}</prv_txn>\n  <sum>150000</sum>\n  <result>0</result>\n"
        f"  {company_bin}\n  <comment>OK</comment>\n</response>\n"
    ).encode()


def refusal_xml(code: int, *, txn_id: str | None = "T1", comment: str | None = None) -> bytes:
    lines = [DECLARATION.decode(), "<response>\n"]
    if txn_id:
        lines.append(f"  <txn_id>{txn_id}</txn_id>\n")
    lines.append(f"  <result>{code}</result>\n")
    if comment:
        lines.append(f"  <comment>{comment}</comment>\n")
    lines.append("</response>\n")
    return "".join(lines).encode()


class KaspiConnection(FakeConnection):
    """Keeps writes until commit, and undoes the writes of a failed savepoint."""

    def __init__(self, responses: dict[str, Any]) -> None:
        super().__init__(responses)
        self.pending: list[str] = []
        self.queries: list[str] = []

    def _answer(self, function: str, args: tuple[Any, ...]) -> list[dict[str, Any]]:
        answer = super()._answer(function, args)
        if function in WRITES:
            self.pending.append(function)
        return answer

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        self.queries.append(query)
        return await super().fetch(query, *args)

    async def execute(self, query: str, *args: Any) -> None:
        self.queries.append(query)
        await super().execute(query, *args)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        savepoint = len(self.pending)
        try:
            yield
        except Exception:
            del self.pending[savepoint:]
            raise

    def functions(self) -> list[str]:
        return [function for function, _ in self.calls]

    def args_of(self, function: str) -> tuple[Any, ...]:
        return next(args for name, args in self.calls if name == function)


class Database:
    """The request transaction and the connections ``committed()`` takes from the pool."""

    def __init__(self) -> None:
        self.responses: dict[str, Any] = {
            CHECK: [{"id": 0}],
            REQUEST_INFO: [REQUEST_INFO_ROW],
            REQUEST_PAY: [{"id": 54321}],
            LOCK: [{"pg_advisory_xact_lock": ""}],
            AFTER: [{"id": 0}],
            LOG: [{"id": 0}],
        }
        self.request = KaspiConnection(self.responses)
        self.separate = KaspiConnection(self.responses)
        self.committed: list[str] = []
        self.events: list[str] = []

    @asynccontextmanager
    async def _acquire(self) -> AsyncIterator[KaspiConnection]:
        yield self.separate

    def acquire(self) -> Any:
        return self._acquire()

    def rows_logged_separately(self) -> list[tuple[Any, ...]]:
        return [args for name, args in self.separate.calls if name == LOG]

    def logged(self) -> tuple[Any, ...]:
        return self.request.args_of(LOG)


@pytest.fixture(autouse=True)
def proxy_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kaspi_proxy_config, "secret", PROXY_SECRET)


@pytest.fixture
def db() -> Database:
    return Database()


@pytest.fixture
async def client(db: Database, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    async def _connection() -> AsyncIterator[KaspiConnection]:
        try:
            yield db.request
        except Exception:
            db.events.append("rollback")
            raise
        db.committed.extend(db.request.pending)
        db.events.append("commit")

    async def _pool() -> Database:
        return db

    monkeypatch.setattr(repository.base, "get_db_pool", _pool)
    app.dependency_overrides[get_db_connection] = _connection
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers=PROXY_HEADERS,
    ) as http:
        yield http
    app.dependency_overrides.clear()


def _assert_xml(response: Any, body: bytes) -> None:
    assert response.status_code == 200
    assert response.content == body
    assert response.headers["content-type"] == XML_TYPE


# --- check ------------------------------------------------------------------------------


async def test_request_check_answers_like_php(client: AsyncClient, db: Database) -> None:
    response = await client.get(REQUEST_ROUTE, params=CHECK_QUERY)

    _assert_xml(response, REQUEST_CHECK_XML)
    assert response.headers["cache-control"] == "no-store, no-cache, must-revalidate"
    assert db.request.functions() == [CHECK, REQUEST_INFO, LOG]
    assert db.request.args_of(CHECK) == ("77", None, ACCEPTED["iin"], "check", "1", "150000", "T1")
    assert db.request.args_of(REQUEST_INFO) == ("77", ACCEPTED["iin"])
    assert db.committed == [LOG]


async def test_check_with_empty_values_writes_empty_elements(
    client: AsyncClient,
    db: Database,
) -> None:
    db.responses[REQUEST_INFO] = [dict.fromkeys(REQUEST_INFO_ROW)]

    response = await client.get(REQUEST_ROUTE, params={"command": "check"})

    _assert_xml(
        response,
        (
            DECLARATION.decode()
            + "<response>\n  <txn_id/>\n  <result>0</result>\n  <bin/>\n  <fields>\n"
            '    <field1 name="ФИО"/>\n'
            '    <field2 name="Номер договора"/>\n'
            '    <field3 name="Дата договора"/>\n'
            '    <field4 name="ЖК"/>\n'
            '    <field5 name="Название компании"/>\n'
            "  </fields>\n  <comment/>\n</response>\n"
        ).encode(),
    )
    assert db.request.args_of(CHECK)[:3] == ("0", None, "")


async def test_check_survives_a_failed_info_read_as_php_did(
    client: AsyncClient,
    db: Database,
) -> None:
    db.responses[REQUEST_INFO] = RaiseError("broken")

    response = await client.get(REQUEST_ROUTE, params=CHECK_QUERY)

    assert response.status_code == 200
    assert b"<bin/>" in response.content
    assert db.logged()[-1] == "1"


async def test_unknown_account_is_refused_and_the_log_row_survives(
    client: AsyncClient,
    db: Database,
) -> None:
    db.responses[CHECK] = [{"id": 1}]

    response = await client.get(REQUEST_ROUTE, params=CHECK_QUERY)

    _assert_xml(response, refusal_xml(1))
    assert db.events == ["rollback"]
    assert db.committed == []
    [row] = db.rows_logged_separately()
    assert row[0] == "check"
    assert row[2] == refusal_xml(1).decode()
    assert row[3:] == ("77", "T1", "150000", None, "1", "2")


# --- pay --------------------------------------------------------------------------------


async def test_request_pay_keeps_the_order_of_php(client: AsyncClient, db: Database) -> None:
    response = await client.get(REQUEST_ROUTE, params=PAY)

    _assert_xml(response, pay_xml(54321))
    assert db.request.functions() == [LOCK, CHECK, REQUEST_PAY, AFTER, REQUEST_INFO, LOG]
    assert db.request.args_of(REQUEST_PAY) == (
        "77",
        ACCEPTED["iin"],
        "150000",
        "T1",
        "20261102120000",
    )
    assert db.request.args_of(AFTER) == ("54321",)
    assert db.committed == [REQUEST_PAY, AFTER, LOG]
    assert db.logged()[3:] == ("77", "T1", "150000", "20261102120000", "1", "1")


async def test_repeated_pay_is_refused_and_never_pays(client: AsyncClient, db: Database) -> None:
    db.responses[CHECK] = [{"id": 3}]

    response = await client.get(REQUEST_ROUTE, params=PAY)

    _assert_xml(response, refusal_xml(3))
    assert db.request.functions() == [LOCK, CHECK]
    assert db.committed == []
    [row] = db.rows_logged_separately()
    # PHP logs no date on this path, unlike the database error below.
    assert row[6] is None
    assert row[-1] == "2"


async def test_lock_is_keyed_on_the_account_and_comes_before_the_check(
    client: AsyncClient,
    db: Database,
) -> None:
    await client.get(REQUEST_ROUTE, params=PAY)

    digest = hashlib.sha256(b"kaspi:77").digest()
    key = int.from_bytes(digest[:8], "big", signed=True)
    assert db.request.functions()[:2] == [LOCK, CHECK]
    assert db.request.args_of(LOCK) == (key,)
    assert db.request.queries[0] == f"select {LOCK}($1)"


async def test_check_takes_no_lock(client: AsyncClient, db: Database) -> None:
    await client.get(REQUEST_ROUTE, params=CHECK_QUERY)

    assert LOCK not in db.request.functions()


async def test_lock_key_fits_a_signed_bigint_and_differs_by_account() -> None:
    keys = {account_lock_key(str(account)) for account in range(1000)}

    assert len(keys) == 1000
    assert all(-(2**63) <= key < 2**63 for key in keys)
    assert any(key < 0 for key in keys)


async def test_lock_failure_pays_nothing_and_is_no_kaspi_answer(
    client: AsyncClient,
    db: Database,
) -> None:
    db.responses[LOCK] = LockNotAvailableError("could not obtain lock")

    response = await client.get(REQUEST_ROUTE, params=PAY)

    assert response.status_code == 500
    assert db.request.functions() == [LOCK]
    assert db.events == ["rollback"]


async def test_pay_twice_stops_the_second_time(client: AsyncClient, db: Database) -> None:
    await client.get(REQUEST_ROUTE, params=PAY)
    db.responses[CHECK] = [{"id": 3}]

    response = await client.get(REQUEST_ROUTE, params=PAY)

    _assert_xml(response, refusal_xml(3))
    assert db.request.functions().count(REQUEST_PAY) == 1


async def test_check_failure_on_pay_gives_the_error_text(client: AsyncClient, db: Database) -> None:
    db.responses[CHECK] = InvalidTextRepresentationError(
        'invalid input syntax for type integer: "abc"',
    )

    response = await client.get(REQUEST_ROUTE, params=PAY)

    comment = (
        "Произошла необработанная ошибка в БД SQLSTATE[22P02]: Invalid text representation: "
        '7 ERROR:  invalid input syntax for type integer: "abc"'
    )
    _assert_xml(response, refusal_xml(5, comment=comment))
    assert db.request.functions() == [LOCK, CHECK]
    assert db.rows_logged_separately()[0][6] == PAY["txn_date"]


async def test_raised_text_goes_to_the_comment(client: AsyncClient, db: Database) -> None:
    db.responses[CHECK] = RaiseError("{Текст ошибки}")

    response = await client.get(REQUEST_ROUTE, params=CHECK_QUERY)

    _assert_xml(response, refusal_xml(5, comment="Текст ошибки"))


async def test_failed_request_pay_rolls_back_and_logs_the_failed_call(
    client: AsyncClient,
    db: Database,
) -> None:
    db.responses[REQUEST_PAY] = InvalidDatetimeFormatError('invalid value "1-" for "DD"')

    response = await client.get(REQUEST_ROUTE, params=PAY)

    # PHP read the error of the earlier call here, so there is no comment.
    _assert_xml(response, refusal_xml(5))
    assert db.events == ["rollback"]
    assert db.committed == []
    assert AFTER not in db.request.functions()
    [row] = db.rows_logged_separately()
    logged_call = json.loads(row[1])
    assert list(logged_call) == ["value", "notice", "error", "error_debug", "status"]
    assert logged_call["error_debug"] == (
        'SQLSTATE[22007]: Invalid datetime format: 7 ERROR:  invalid value "1-" for "DD"'
    )
    assert (
        logged_call["error"] == f"Произошла необработанная ошибка в БД {logged_call['error_debug']}"
    )
    assert row[1].startswith('{"value":null,"notice":[],"error":"\\u041f')
    assert row[6] == PAY["txn_date"]


async def test_concurrent_pay_with_the_same_txn_id_records_nothing(
    client: AsyncClient,
    db: Database,
) -> None:
    db.responses[REQUEST_PAY] = UniqueViolationError("duplicate key")

    response = await client.get(REQUEST_ROUTE, params=PAY)

    _assert_xml(response, refusal_xml(5))
    assert db.committed == []


async def test_failed_after_payment_operations_keeps_the_payment(
    client: AsyncClient,
    db: Database,
) -> None:
    db.responses[AFTER] = RaiseError("broken")

    response = await client.get(REQUEST_ROUTE, params=PAY)

    _assert_xml(response, pay_xml(54321))
    assert db.committed == [REQUEST_PAY, LOG]


async def test_failed_info_after_pay_keeps_the_payment_with_an_empty_bin(
    client: AsyncClient,
    db: Database,
) -> None:
    db.responses[REQUEST_INFO] = RaiseError("broken")

    response = await client.get(REQUEST_ROUTE, params=PAY)

    _assert_xml(response, pay_xml(54321, company_bin="<bin/>"))
    assert db.committed == [REQUEST_PAY, AFTER, LOG]


async def test_failed_log_after_pay_keeps_the_payment_and_the_answer(
    client: AsyncClient,
    db: Database,
) -> None:
    db.responses[LOG] = RaiseError("broken")

    response = await client.get(REQUEST_ROUTE, params=PAY)

    _assert_xml(response, pay_xml(54321))
    assert db.committed == [REQUEST_PAY, AFTER]


async def test_failed_log_of_a_refusal_keeps_the_answer(client: AsyncClient, db: Database) -> None:
    db.responses[CHECK] = [{"id": 1}]
    db.responses[LOG] = RaiseError("broken")

    response = await client.get(REQUEST_ROUTE, params=CHECK_QUERY)

    _assert_xml(response, refusal_xml(1))


async def _send_request(query: bytes) -> list[str]:
    messages: list[str] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        messages.append(message["type"])

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": REQUEST_ROUTE,
        "raw_path": REQUEST_ROUTE.encode(),
        "root_path": "",
        "query_string": query,
        "headers": [(b"x-kaspi-proxy-secret", PROXY_SECRET.encode())],
        "client": ("testclient", 1),
        "server": ("test", 80),
    }
    await app(scope, receive, send)
    return messages


async def test_commit_comes_before_the_answer(db: Database) -> None:
    order: list[str] = []

    async def _connection() -> AsyncIterator[KaspiConnection]:
        yield db.request
        order.append("commit")

    app.dependency_overrides[get_db_connection] = _connection
    try:
        messages = await _send_request(b"command=foo")
    finally:
        app.dependency_overrides.clear()
    assert order == ["commit"]
    assert messages[0] == "http.response.start"


async def test_failed_commit_is_not_answered_as_paid(db: Database) -> None:
    async def _connection() -> AsyncIterator[KaspiConnection]:
        yield db.request
        raise ConnectionError("commit failed")

    app.dependency_overrides[get_db_connection] = _connection
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as http:
            response = await http.get(REQUEST_ROUTE, params=PAY, headers=PROXY_HEADERS)
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 500
    assert b"<prv_txn>" not in response.content


# --- parameters and addresses -----------------------------------------------------------


@pytest.mark.parametrize(
    "query",
    ["", "?command=foo", "?command=Check", "?command[]=check"],
)
async def test_unknown_command_gives_an_empty_page(
    client: AsyncClient,
    db: Database,
    query: str,
) -> None:
    response = await client.get(REQUEST_ROUTE + query)

    assert response.status_code == 200
    assert response.content == b""
    assert response.headers["content-type"] == HTML_TYPE
    assert db.request.calls == []


async def test_repeated_address_key_is_an_array(client: AsyncClient, db: Database) -> None:
    response = await client.get(f"{REQUEST_ROUTE}/command/check/command/check")

    assert response.content == b""
    assert db.request.calls == []


@pytest.mark.parametrize("command", ["check", "pay"])
async def test_array_txn_id_never_reaches_the_database(
    client: AsyncClient,
    db: Database,
    command: str,
) -> None:
    response = await client.get(f"{REQUEST_ROUTE}?command={command}&txn_id[]=1&account=1")

    assert response.status_code == 200
    assert response.headers["content-type"] == HTML_TYPE
    assert db.request.calls == []


@pytest.mark.parametrize(
    "url",
    [
        f"{REQUEST_ROUTE}/?command=check&account=9&txn_id=T1&iin=1&sum=2",
        f"{REQUEST_ROUTE}/command/check/account/9/txn_id/T1/iin/1/sum/2",
        f"{REQUEST_ROUTE}/command/check/account/9/txn_id/T1/iin/1/sum/2/",
        f"{REQUEST_ROUTE}/command/check/account/9/txn_id/T1?iin=1&sum=2&account=8",
        f"{REQUEST_ROUTE}/command/check/account/9/txn_id/T1/iin/1/sum/2/orphan",
    ],
)
async def test_zend_address_forms(client: AsyncClient, db: Database, url: str) -> None:
    await client.get(url)

    assert db.request.args_of(CHECK) == ("9", None, "1", "check", "1", "2", "T1")


async def test_address_value_is_decoded_as_zend_does(client: AsyncClient, db: Database) -> None:
    db.responses[CHECK] = [{"id": 1}]

    response = await client.get(f"{REQUEST_ROUTE}/command/check/txn_id/a+b%2Fc")

    _assert_xml(response, refusal_xml(1, txn_id="a b/c"))


async def test_address_comes_before_query_and_query_before_form(
    client: AsyncClient,
    db: Database,
) -> None:
    await client.post(
        f"{REQUEST_ROUTE}/txn_id/ADDRESS?txn_id=QUERY&iin=QUERY",
        data={"command": "check", "txn_id": "FORM", "iin": "FORM", "account": "5"},
    )

    assert db.request.args_of(CHECK) == ("5", None, "QUERY", "check", "1", "0", "ADDRESS")


async def test_empty_query_value_does_not_fall_through_to_the_form(
    client: AsyncClient,
    db: Database,
) -> None:
    await client.post(
        f"{REQUEST_ROUTE}?account=",
        data={"command": "check", "account": "5"},
    )

    assert db.request.args_of(CHECK)[0] == "0"


@pytest.mark.parametrize("method", ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"])
async def test_any_http_method_works(client: AsyncClient, db: Database, method: str) -> None:
    response = await client.request(method, f"{REQUEST_ROUTE}?command=check&txn_id=T1")

    assert response.status_code == 200
    assert CHECK in db.request.functions()


async def test_body_is_read_only_for_post(client: AsyncClient, db: Database) -> None:
    response = await client.put(REQUEST_ROUTE, data={"command": "check"})

    assert response.content == b""
    assert db.request.calls == []


async def test_all_parameters_are_logged_in_zend_order(client: AsyncClient, db: Database) -> None:
    await client.post(
        f"{REQUEST_ROUTE}/txn_id/T%2F1?command=check&a.b=1&iin[]=Ж",
        data={"account": "5", "command": "other"},
    )

    assert db.logged()[1] == (
        '{"controller":"client","action":"kaspi-request-pay","txn_id":"T\\/1","module":"default",'
        '"command":"check","a_b":"1","iin":["\\u0416"],"account":"5"}'
    )


@pytest.mark.parametrize("name", ["account", "iin", "sum"])
async def test_array_value_goes_to_the_database_as_the_word_array(
    client: AsyncClient,
    db: Database,
    name: str,
) -> None:
    await client.get(f"{REQUEST_ROUTE}?command=check&{name}[]=1")

    assert "Array" in db.request.args_of(CHECK)


async def test_integer_and_sum_are_converted_by_the_server(
    client: AsyncClient,
    db: Database,
) -> None:
    await client.get(f"{REQUEST_ROUTE}?command=check&account=abc&sum=x")

    assert db.request.args_of(CHECK)[0] == "abc"
    check_query = next(query for query in db.request.queries if CHECK in query)
    assert check_query == (
        "select client.kaspi_check($1::text::integer, $2::text::integer, $3, $4, "
        "$5::text::integer, $6::text::double precision, $7)"
    )


async def test_invalid_utf8_gets_the_database_error_and_stays_in_the_answer(
    client: AsyncClient,
    db: Database,
) -> None:
    response = await client.get(f"{REQUEST_ROUTE}?command=check&txn_id=%FF&iin=%D0A")

    comment = (
        "Произошла необработанная ошибка в БД SQLSTATE[22021]: Character not in repertoire: "
        '7 ERROR:  invalid byte sequence for encoding "UTF8": 0xd0 0x41'
    )
    expected = refusal_xml(5, txn_id="@", comment=comment).replace(b"@", b"\xff")
    _assert_xml(response, expected)
    assert db.request.calls == []
    # json_encode() fails on these bytes: PHP logged an empty string, then the bytes broke the row.
    assert db.separate.calls == []


async def test_nul_byte_ends_the_value_as_in_c(client: AsyncClient, db: Database) -> None:
    db.responses[CHECK] = [{"id": 1}]

    response = await client.get(f"{REQUEST_ROUTE}?command=check&txn_id=a%00b")

    _assert_xml(response, refusal_xml(1, txn_id="a"))
    assert db.request.args_of(CHECK)[-1] == "a"


@pytest.mark.parametrize(
    ("txn_id", "element"),
    [
        ("0", b"<txn_id>0</txn_id>"),
        ("a%26b", b"<txn_id/>"),
        ("a%3Cb%3E", b"<txn_id>a&lt;b&gt;</txn_id>"),
        ("%26lt;%26foo;%26%2365;%26%23xZ;%0D%22", b'<txn_id>&lt;&foo;AZ;&#13;"</txn_id>'),
        ("x%26foo;y%26z", b"<txn_id>x&foo;</txn_id>"),
        ("%26%23x41;%26%2366;%26%230;%26;%26amp;", b"<txn_id>AB&amp;</txn_id>"),
        ("%26%23x110000;", b"<txn_id/>"),
    ],
)
async def test_values_pass_through_libxml_entity_parsing(
    client: AsyncClient,
    db: Database,
    txn_id: str,
    element: bytes,
) -> None:
    db.responses[CHECK] = [{"id": 1}]

    response = await client.get(f"{REQUEST_ROUTE}?command=check&txn_id={txn_id}")

    assert response.content == DECLARATION + b"<response>\n  " + element + (
        b"\n  <result>1</result>\n</response>\n"
    )


@pytest.mark.parametrize("query", ["", "?txn_id="])
async def test_empty_txn_id_is_left_out_of_a_refusal(
    client: AsyncClient,
    db: Database,
    query: str,
) -> None:
    db.responses[CHECK] = [{"id": 1}]

    response = await client.get(f"{REQUEST_ROUTE}/command/check{query}")

    _assert_xml(response, refusal_xml(1, txn_id=None))


# --- sender gate ------------------------------------------------------------------------


GATED_REQUESTS = [
    ("GET", REQUEST_ROUTE),
    ("GET", f"{REQUEST_ROUTE}/"),
    ("GET", f"{REQUEST_ROUTE}/command/pay/account/1"),
    ("POST", REQUEST_ROUTE),
    ("HEAD", REQUEST_ROUTE),
    ("PUT", f"{REQUEST_ROUTE}/command/pay"),
]


@pytest.mark.parametrize(("method", "url"), GATED_REQUESTS)
@pytest.mark.parametrize(
    "headers",
    [
        {"X-Kaspi-Proxy-Secret": ""},
        {"X-Kaspi-Proxy-Secret": "wrong"},
        {"X-Kaspi-Proxy-Secret": "ключ".encode()},
    ],
)
async def test_request_without_the_right_secret_is_forbidden_before_anything_runs(
    client: AsyncClient,
    db: Database,
    method: str,
    url: str,
    headers: dict[str, Any],
) -> None:
    response = await client.request(method, url, headers=headers, params=PAY)

    assert response.status_code == 403
    assert db.events == []
    assert db.request.calls == []


async def test_missing_secret_header_is_forbidden(db: Database) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        response = await http.get(
            REQUEST_ROUTE,
            params=PAY,
            headers={"X-Forwarded-For": "194.187.247.152"},
        )

    assert response.status_code == 403
    assert db.request.calls == []


@pytest.mark.parametrize(("method", "url"), GATED_REQUESTS)
async def test_unconfigured_secret_gives_503_even_with_a_header(
    client: AsyncClient,
    db: Database,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    url: str,
) -> None:
    monkeypatch.setattr(kaspi_proxy_config, "secret", "")

    response = await client.request(method, url, params=PAY)

    assert response.status_code == 503
    assert db.events == []


@pytest.mark.parametrize("route", ["kaspi-payment-pay", "kaspi-pay-form"])
async def test_removed_routes_are_gone(client: AsyncClient, route: str) -> None:
    response = await client.get(f"/api/kaspi/{route}")

    assert response.status_code == 404
