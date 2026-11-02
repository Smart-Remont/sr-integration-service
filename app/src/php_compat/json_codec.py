"""JSON exactly as the PHP office produced it: partners moved from PHP see the same bytes.

Values decoded from JSON keep their JSON types and follow ``json_decode($text, true)``.
"""

import json


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


def php_json_encode_unescaped(value: object) -> str:
    """``json_encode($value, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE)``."""
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return _escape_line_separators(text)


def php_json_encode_default(value: object) -> str:
    """``json_encode($value)`` without flags. Invalid UTF-8 gives ``false``, which PDO sends as ``''``."""
    text = json.dumps(_as_php_array(value), ensure_ascii=False, separators=(",", ":"))
    try:
        text.encode()
    except UnicodeEncodeError:
        return ""
    return json.dumps(_as_php_array(value), separators=(",", ":")).replace("/", "\\/")


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
