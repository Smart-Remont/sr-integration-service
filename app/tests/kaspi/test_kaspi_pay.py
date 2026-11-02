"""``kaspi-request-pay``, command ``pay``. Expected bytes for errors were checked against the PHP
stand, and the pay answers follow the rows PHP left in ``kaspi_pay_log_tab``. The pay command is
never run on the stand, so it is covered only here.
"""

import hashlib
import json
from collections.abc import AsyncIterator
from typing import Any

from asyncpg.exceptions import (
    InvalidDatetimeFormatError,
    InvalidTextRepresentationError,
    LockNotAvailableError,
    RaiseError,
    UniqueViolationError,
)
from httpx import ASGITransport, AsyncClient

from src.database.deps import get_db_connection
from src.features.kaspi.repo import account_lock_key
from src.main import app
from tests.kaspi.fakes import (
    ACCEPTED,
    AFTER,
    CHECK,
    CHECK_QUERY,
    LOCK,
    LOG,
    PAY,
    PROXY_HEADERS,
    PROXY_SECRET,
    REQUEST_INFO,
    REQUEST_PAY,
    REQUEST_ROUTE,
    Database,
    KaspiConnection,
    _assert_xml,
    pay_xml,
    refusal_xml,
)


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
