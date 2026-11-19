"""JSON exactly as the PHP office produced it: partners moved from PHP see the same bytes.

Values decoded from JSON keep their JSON types and follow ``json_decode($text, true)``.
"""

import json
import re

_INT64_MIN = -(2**63)
_INT64_MAX = 2**63 - 1
# json_decode with its default depth of 512 accepts this much nesting, checked on PHP 7.4.
_MAX_NESTING = 511
_LONE_SURROGATE = re.compile("[\ud800-\udfff]")


def php_json_decode(text: str | bytes) -> object | None:
    """``json_decode($text, true)``: broken JSON is ``null``, objects become PHP arrays.

    PHP also refuses what Python takes: a BOM or UTF-16 in bytes, an unpaired ``\\uD83D``, deep
    nesting. An integer over 64 bits becomes a float.
    """
    return _as_php_array(php_json_decode_objects(text))


def php_json_decode_objects(text: str | bytes) -> object | None:
    """``json_decode($text)``: an object stays an object, also an empty one or one keyed 0..n-1."""
    try:
        raw = text.decode() if isinstance(text, bytes) else text
        value = json.loads(raw, parse_constant=_reject_constant, parse_int=_php_int)
    except ValueError, RecursionError:
        return None
    return value if _php_accepts(value) else None


def php_json_encode(value: object) -> bytes:
    """``json_encode($value, JSON_UNESCAPED_UNICODE)``, used by ``sendResponse``."""
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return _escape_line_separators(text).replace("/", "\\/").encode()


def php_json_encode_pretty(value: object) -> bytes:
    """``JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT``: ``response_json``."""
    return _escape_line_separators(json.dumps(value, ensure_ascii=False, indent=4)).encode()


def php_json_encode_unescaped(value: object) -> str:
    """``json_encode($value, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE)``."""
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return _escape_line_separators(text)


def php_json_encode_default(value: object) -> str:
    """``json_encode($value)`` without flags. Invalid UTF-8 gives ``false``, which PDO sends as ``''``."""
    plain = _whole_floats_as_int(_as_php_array(value))
    try:
        json.dumps(plain, ensure_ascii=False, allow_nan=False).encode()
    except UnicodeEncodeError, ValueError:
        # ValueError: PHP gives ``false`` for INF and NAN as well.
        return ""
    return json.dumps(plain, separators=(",", ":")).replace("/", "\\/")


def _as_php_array(value: object) -> object:
    if isinstance(value, list):
        return [_as_php_array(item) for item in value]
    if not isinstance(value, dict):
        return value
    # An empty object and an object keyed 0..n-1 are lists once they pass through a PHP array.
    if list(value) == [str(index) for index in range(len(value))]:
        return [_as_php_array(item) for item in value.values()]
    return {key: _as_php_array(item) for key, item in value.items()}


def _whole_floats_as_int(value: object) -> object:
    """PHP writes 1.0 as ``1``. From 1e16 Python gives the same digits with an exponent."""
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e16:
        return int(value)
    if isinstance(value, list):
        return [_whole_floats_as_int(item) for item in value]
    if isinstance(value, dict):
        return {key: _whole_floats_as_int(item) for key, item in value.items()}
    return value


def _php_int(text: str) -> int | float:
    number = int(text)
    return number if _INT64_MIN <= number <= _INT64_MAX else float(text)


def _php_accepts(value: object) -> bool:
    pending = [(value, 0)]
    while pending:
        item, nesting = pending.pop()
        if isinstance(item, str):
            if _LONE_SURROGATE.search(item):
                return False
        elif isinstance(item, list | dict):
            if nesting >= _MAX_NESTING:
                return False
            children = item if isinstance(item, list) else [*item, *item.values()]
            pending.extend((child, nesting + 1) for child in children)
    return True


def _reject_constant(name: str) -> None:
    raise ValueError(name)


def _escape_line_separators(text: str) -> str:
    return text.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
