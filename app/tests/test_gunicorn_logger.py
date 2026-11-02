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


def test_callback_key_is_masked_and_other_addresses_stay() -> None:
    masked = access_record("/api/client_sign/callback/key/abc123")
    assert SecretPathFilter().filter(masked)
    assert (
        masked.getMessage() == '127.0.0.1:1 - "POST /api/client_sign/callback/key/*** HTTP/1.1" 200'
    )

    other = access_record("/api/client_sign/download/uuid/u")
    assert SecretPathFilter().filter(other)
    assert (
        other.getMessage() == '127.0.0.1:1 - "POST /api/client_sign/download/uuid/u HTTP/1.1" 200'
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
