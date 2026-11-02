"""JSON exactly as the PHP office produced it: partners moved from PHP see the same bytes.

Stored-function values arrive in PHP as PostgreSQL text through PDO, so a row is converted once,
when it is read (``pg_row``). Only integers and booleans stay native there. Values decoded from
JSON are not rows: they keep their JSON types and follow ``json_decode($text, true)``.
"""

import json
import re
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from uuid import UUID

# PostgreSQL prints timestamptz in the session time zone; prod uses Asia/Aqtau.
_DB_TIME_ZONE = timezone(timedelta(hours=5))
_FLOAT_PLAIN_LIMIT = 1e15
_ARRAY_PLAIN_ITEM = re.compile(r"[^\s{},\"\\]+")


def pg_row(row: dict[str, object]) -> dict[str, object]:
    return {column: _pg_text(value) for column, value in row.items()}


def php_json_decode(text: str | bytes) -> object | None:
    """``json_decode($text, true)``: broken JSON is ``null``, objects become PHP arrays."""
    try:
        return _as_php_array(json.loads(text, parse_constant=_reject_constant))
    except ValueError:
        return None


def php_json_encode(value: object) -> bytes:
    """``json_encode($value, JSON_UNESCAPED_UNICODE)``, used by ``sendResponse``."""
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return _escape_line_separators(text).replace("/", "\\/").encode()


def php_json_encode_pretty(value: object) -> bytes:
    """``JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT``: ``response_json``."""
    return _escape_line_separators(json.dumps(value, ensure_ascii=False, indent=4)).encode()


def php_json_encode_default(value: object) -> str:
    """``json_encode($value)`` without flags. Invalid UTF-8 gives ``false``, which PDO sends as ``''``."""
    text = json.dumps(_as_php_array(value), ensure_ascii=False, separators=(",", ":"))
    try:
        text.encode()
    except UnicodeEncodeError:
        return ""
    return json.dumps(_as_php_array(value), separators=(",", ":")).replace("/", "\\/")


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


def _as_php_array(value: object) -> object:
    if isinstance(value, list):
        return [_as_php_array(item) for item in value]
    if not isinstance(value, dict):
        return value
    # An empty object and an object keyed 0..n-1 are lists once they pass through a PHP array.
    if list(value) == [str(index) for index in range(len(value))]:
        return [_as_php_array(item) for item in value.values()]
    return {key: _as_php_array(item) for key, item in value.items()}


def _reject_constant(name: str) -> None:
    raise ValueError(name)


def _escape_line_separators(text: str) -> str:
    return text.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
