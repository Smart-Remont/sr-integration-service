"""Parameters and addresses of ``kaspi-request-pay``, read the way Zend and PHP read them."""

import pytest
from httpx import AsyncClient

from tests.kaspi.fakes import (
    CHECK,
    DECLARATION,
    HTML_TYPE,
    REQUEST_ROUTE,
    Database,
    _assert_xml,
    refusal_xml,
)


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
