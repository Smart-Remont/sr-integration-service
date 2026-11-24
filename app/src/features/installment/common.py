"""What the Freedom and BI Finance flows share: the event log, the bank's Basic login on our
hooks, the deal checks before a bank call and the borrower's IIN."""

import base64
import binascii
import re
from secrets import compare_digest
from typing import Any

from asyncpg.exceptions import PostgresError
from bcrypt import checkpw
from fastapi import HTTPException, status
from loguru import logger

from src.shared.deal_guard import check_deal_client_request_state

from .repo import InstallmentRepository, WebhookCredential

_PG_RAISE_MESSAGE_RE = re.compile(r"\{([^}]*)\}")

_PII_KEYS = frozenset(
    {
        "iin",
        "mobile_phone",
        "mobilephone",
        "phone",
        "prop_iin",
        "prop_phone",
        "first_name",
        "last_name",
        "middle_name",
        "fio",
    }
)


def redact_pii(value: Any) -> Any:
    """The event log keeps what was sent and received, without the borrower's IIN, phone or name."""
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            lowered = key.lower()
            if lowered in _PII_KEYS or lowered.endswith("_iin") or "phone" in lowered:
                redacted[key] = "***"
            else:
                redacted[key] = redact_pii(item)
        return redacted
    if isinstance(value, list):
        return [redact_pii(item) for item in value]
    return value


def extract_error_message(exc: BaseException) -> str:
    """The text a manager can read: HTTPException detail or the {…} part of a RAISE EXCEPTION."""
    if isinstance(exc, HTTPException):
        return str(exc.detail)
    cause: BaseException | None = exc
    while cause is not None:
        if isinstance(cause, PostgresError):
            text = str(cause)
            match = _PG_RAISE_MESSAGE_RE.search(text)
            return match.group(1) if match else text
        cause = cause.__cause__
    return str(exc)


def normalize_iin(iin: str | None) -> str | None:
    if iin is None:
        return None
    normalized = "".join(symbol for symbol in iin if symbol.isdigit())
    return normalized if len(normalized) == 12 else None


def parse_basic_authorization_header(header_value: str | None) -> tuple[str | None, str | None]:
    if header_value is None or not header_value.startswith("Basic "):
        return None, None
    token = header_value[6:].strip()
    if not token:
        return None, None
    try:
        decoded = base64.b64decode(token, validate=True).decode("utf-8")
    except binascii.Error, UnicodeDecodeError:
        return None, None
    if ":" not in decoded:
        return None, None
    username, password = decoded.split(":", 1)
    return username, password


def verify_webhook_password(password: str, credentials: WebhookCredential) -> bool:
    try:
        return checkpw(password.encode(), credentials.password_hash.encode())
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Invalid webhook password hash configuration.",
        ) from exc


def invalid_webhook_credentials() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid credentials",
        headers={"WWW-Authenticate": "Basic"},
    )


def check_webhook_login(credentials: WebhookCredential, authorization_header: str | None) -> None:
    """The bank's Basic login against webhook_username / bcrypt webhook_password of the provider."""
    username, password = parse_basic_authorization_header(authorization_header)
    if username is None or password is None:
        raise invalid_webhook_credentials()
    username_ok = compare_digest(username.encode(), credentials.username.encode())
    password_ok = verify_webhook_password(password, credentials)
    if not (username_ok and password_ok):
        raise invalid_webhook_credentials()


async def log_event(
    repository: InstallmentRepository,
    event_type: str,
    *,
    source: str,
    installment_id: int | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    """Committed on its own: an HTTP error later in the request must not erase the audit of
    what was sent to and received from the bank. A failed write is only logged: the audit
    must not break the call it describes."""
    redacted = redact_pii(payload or {})
    try:
        await repository.committed(
            lambda repo: repo.insert_event_log(
                installment_id=installment_id,
                event_type=event_type,
                payload=redacted,
                source=source,
            )
        )
    except Exception as exc:
        logger.warning(
            "Event log write failed | event_type={event_type} installment_id={installment_id} error={error}",
            event_type=event_type,
            installment_id=installment_id,
            error=str(exc),
        )


async def require_deal_main_client_request(
    repository: InstallmentRepository, client_request_id: int
) -> None:
    state = await repository.get_deal_client_request_state(client_request_id)
    check_deal_client_request_state(state, client_request_id)
