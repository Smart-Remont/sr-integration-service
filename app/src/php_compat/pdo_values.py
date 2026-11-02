"""Values as PDO carried them between PHP and PostgreSQL.

Stored-function values arrive in PHP as PostgreSQL text through PDO, so a row is converted once,
when it is read (``pg_row``). Only integers and booleans stay native there.
"""

import re
from collections.abc import Callable
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from .pdo_errors import php_invalid_encoding_message

# PostgreSQL prints timestamptz in the session time zone; prod uses Asia/Aqtau.
_DB_TIME_ZONE = timezone(timedelta(hours=5))
_FLOAT_PLAIN_LIMIT = 1e15
_ARRAY_PLAIN_ITEM = re.compile(r"[^\s{},\"\\]+")


def pg_row(row: dict[str, object]) -> dict[str, object]:
    return {column: _pg_text(value) for column, value in row.items()}


def pdo_text(value: object) -> str | None:
    """PDO sends every value as a string and null as NULL.

    Only for text and integers: for a boolean or a float PHP prints what ``php_string`` gives.
    """
    return None if value is None else str(value)


def pdo_bound_text(
    value: str | None,
    invalid_encoding: Callable[[str], Exception],
) -> str | None:
    """A text parameter as libpq takes it. ``invalid_encoding`` gets the PDO message of a refusal."""
    if value is None:
        return None
    # libpq takes a C string: the value ends at the first NUL byte.
    text = value.partition("\0")[0]
    message = php_invalid_encoding_message(text)
    if message:
        raise invalid_encoding(message)
    return text


def _pg_text(value: object) -> object:
    match value:
        case bool() | int() | str() | None:
            return value
        case datetime():
            return _datetime_text(value)
        case date():
            return value.isoformat()
        case time():
            return value.strftime("%H:%M:%S")
        case Decimal():
            return format(value, "f")
        case float():
            return _float_text(value)
        case UUID():
            return str(value)
        case list() | tuple():
            return _array_text(value)
        case _:
            return str(value)


def _datetime_text(value: datetime) -> str:
    if value.tzinfo is None:
        return _naive_datetime_text(value)
    return f"{_naive_datetime_text(value.astimezone(_DB_TIME_ZONE))}+05"


def _naive_datetime_text(value: datetime) -> str:
    text = value.strftime("%Y-%m-%d %H:%M:%S")
    if value.microsecond:
        text += f".{value.microsecond:06d}".rstrip("0")
    return text


def _float_text(value: float) -> str:
    if not value.is_integer():
        return repr(value)
    if abs(value) < _FLOAT_PLAIN_LIMIT:
        return str(int(value))
    mantissa, exponent = f"{value:.16e}".split("e")
    return f"{mantissa.rstrip('0').rstrip('.')}e{exponent}"


def _array_text(items: list[object] | tuple[object, ...]) -> str:
    return "{" + ",".join(_array_item_text(item) for item in items) + "}"


def _array_item_text(item: object) -> str:
    if item is None:
        return "NULL"
    if isinstance(item, bool):
        return "t" if item else "f"
    text = _pg_text(item)
    text = text if isinstance(text, str) else str(text)
    if isinstance(item, list | tuple) or _ARRAY_PLAIN_ITEM.fullmatch(text):
        return text if text.upper() != "NULL" else f'"{text}"'
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
