import logging

import pytest

from _gunicorn.logger import SecretPathFilter


def access_record(path: str) -> logging.LogRecord:
    return logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:1", "POST", path, "1.1", 200),
        None,
    )


def test_other_addresses_stay() -> None:
    other = access_record("/api/big_integration/request-info?x=1")
    assert SecretPathFilter().filter(other)
    assert (
        other.getMessage()
        == '127.0.0.1:1 - "POST /api/big_integration/request-info?x=1 HTTP/1.1" 200'
    )


@pytest.mark.parametrize(
    "tail",
    [
        "?token=abc",
        "?x=1&token=abc&y=2",
        "?token=a&token=abc",
        "?%74oken=abc",
        "?token=ab/abc",
        "/token/abc",
        "/token/a/token/abc",
    ],
)
def test_nothing_after_the_albato_address_reaches_the_log(tail: str) -> None:
    record = access_record(f"/api/albato/albato-meta-lead{tail}")

    assert SecretPathFilter().filter(record)
    assert (
        record.getMessage() == '127.0.0.1:1 - "POST /api/albato/albato-meta-lead*** HTTP/1.1" 200'
    )


def test_albato_address_without_a_tail_stays_as_it_is() -> None:
    record = access_record("/api/albato/albato-meta-lead")

    assert SecretPathFilter().filter(record)
    assert record.getMessage() == '127.0.0.1:1 - "POST /api/albato/albato-meta-lead HTTP/1.1" 200'


KASPI_IIN = "880101300123"


@pytest.mark.parametrize(
    "tail",
    [
        f"?command=check&account=7&iin={KASPI_IIN}",
        f"/command/check/account/7/iin/{KASPI_IIN}",
        f"/iin/{KASPI_IIN}?iin={KASPI_IIN}",
        f"/?iin={KASPI_IIN}",
        f'?iin="{KASPI_IIN}',
    ],
)
def test_nothing_after_the_kaspi_address_reaches_the_log(tail: str) -> None:
    record = access_record(f"/api/kaspi/kaspi-request-pay{tail}")

    assert SecretPathFilter().filter(record)
    message = record.getMessage()
    assert KASPI_IIN not in message
    assert message == '127.0.0.1:1 - "POST /api/kaspi/kaspi-request-pay*** HTTP/1.1" 200'


CLIENT_SIGN_ROUTES = [
    "sign-doc-list",
    "old-sign-doc-list",
    "perform-sign",
    "callback",
    "download",
    "download-signed",
    "download-cms",
    "download-agreement",
    "download-ds",
]


@pytest.mark.parametrize("route", CLIENT_SIGN_ROUTES)
@pytest.mark.parametrize(
    "tail",
    [
        "?hash=abc123pass",
        "?uuid=abc123pass&doc_type=DS",
        "/hash/abc123pass",
        "/uuid/abc123pass/doc_type/DS",
        "/key/abc123pass",
        "/",
        '?hash="abc123pass',
    ],
)
def test_nothing_after_a_client_sign_address_reaches_the_log(route: str, tail: str) -> None:
    record = access_record(f"/api/client_sign/{route}{tail}")

    assert SecretPathFilter().filter(record)
    assert record.getMessage() == f'127.0.0.1:1 - "POST /api/client_sign/{route}*** HTTP/1.1" 200'


@pytest.mark.parametrize(
    "path",
    [
        "/api/client_sign/abc123pass",
        "/api/client_sign/download-abc123pass",
        "/api/client_sign?hash=abc123pass",
    ],
)
def test_client_sign_address_it_does_not_know_keeps_only_the_prefix(path: str) -> None:
    record = access_record(path)

    assert SecretPathFilter().filter(record)
    assert "abc123pass" not in record.getMessage()


@pytest.mark.parametrize(
    "path",
    [
        "/api/kaspi/kaspi-request-pay",
        *(f"/api/client_sign/{route}" for route in CLIENT_SIGN_ROUTES),
    ],
)
def test_address_without_a_tail_stays_as_it_is(path: str) -> None:
    record = access_record(path)

    assert SecretPathFilter().filter(record)
    assert record.getMessage() == f'127.0.0.1:1 - "POST {path} HTTP/1.1" 200'
