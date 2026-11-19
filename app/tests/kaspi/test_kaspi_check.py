"""``kaspi-request-pay``, command ``check``. Expected bytes were checked against the PHP stand."""

from asyncpg.exceptions import RaiseError
from httpx import AsyncClient

from tests.kaspi.fakes import (
    ACCEPTED,
    CHECK,
    CHECK_QUERY,
    DECLARATION,
    LOG,
    REQUEST_CHECK_XML,
    REQUEST_INFO,
    REQUEST_INFO_ROW,
    REQUEST_ROUTE,
    Database,
    _assert_xml,
    refusal_xml,
)


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
