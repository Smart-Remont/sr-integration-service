import logging

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
