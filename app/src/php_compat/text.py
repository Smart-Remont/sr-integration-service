"""PHP 7.4 string functions and type juggling that pages moved from PHP depend on, byte for byte."""

import html.entities
import math
import re

_TRIM_CHARS = " \t\n\r\0\x0b"
_ENTITY = re.compile(r"&(#[0-9]+|#[xX][0-9a-fA-F]+|[a-zA-Z][a-zA-Z0-9]*);")
_NUMERIC_PREFIX = re.compile(r"[ \t\n\r\v\f]*[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?")
_TAG_NAME = re.compile(r"</?([^\s/>]*)")
_MAX_CODE_POINT = 0x10FFFF
# What PHP prints when an array is used as a string, for example as a SQL parameter.
PHP_ARRAY_TEXT = "Array"


def php_html_escape(value: object) -> str:
    """``htmlspecialchars($value)``: ENT_COMPAT, and invalid UTF-8 gives an empty string."""
    text = php_string(value)
    try:
        text.encode()
    except UnicodeEncodeError:
        return ""
    return (
        text.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")
    )


def php_html_entity_decode(text: str) -> str:
    """``html_entity_decode($text, ENT_QUOTES)``: the HTML 4.01 table, a semicolon is required."""
    return _ENTITY.sub(_decoded_entity, text)


def php_strip_tags(text: str, allowed: frozenset[str] = frozenset()) -> str:
    """``strip_tags``: ``<`` before whitespace is text, quotes hide ``>``, an open tag runs to the end."""
    output: list[str] = []
    index = 0
    while index < len(text):
        character = text[index]
        if character != "<" or text[index + 1 : index + 2].isspace():
            output.append(character)
            index += 1
            continue
        end = _tag_end(text, index + 1)
        if end is None:
            break
        tag = text[index : end + 1]
        if _tag_name(tag) in allowed:
            output.append(tag)
        index = end + 1
    return "".join(output)


def php_trim(text: str) -> str:
    return text.strip(_TRIM_CHARS)


def php_strtoupper(text: str) -> str:
    """Bytes in the C locale: Cyrillic stays as it is."""
    return "".join(c.upper() if c.isascii() else c for c in text)


def php_casefold_equal(left: str, right: str) -> bool:
    """``strcasecmp($left, $right) === 0``: only ASCII letters ignore case."""
    return _ascii_lower(left) == _ascii_lower(right)


def php_is_truthy(value: object) -> bool:
    """``!empty($value)``, and the condition of ``?:``."""
    if isinstance(value, str | bytes):
        return value not in ("", "0", b"", b"0")
    return bool(value)


def is_php_empty(value: object) -> bool:
    """The controller's ``is_null or empty or strlen(trim(...)) === 0``."""
    if isinstance(value, str):
        return php_trim(value) in ("", "0")
    # trim() of an array gives null, so an array of any size counts as empty.
    return isinstance(value, list | dict) or not value


def zero_to_null[T](value: T) -> T | None:
    """PHP ``zeroToNull``, and ``!empty($x) ? $x : null``: an empty value means "not passed"."""
    return value if php_is_truthy(value) else None


def php_string(value: object) -> str:
    """``(string)$value``."""
    match value:
        case None | False:
            return ""
        case True:
            return "1"
        case str():
            return value
        case list() | dict():
            return PHP_ARRAY_TEXT
        case float():
            return _float_text(value)
        case _:
            return str(value)


def php_int(value: object) -> int:
    """``(int)$value``: a string gives its numeric prefix, an array gives 0 or 1."""
    match value:
        case bool() | int():
            return int(value)
        case float():
            return int(value) if math.isfinite(value) else 0
        case str():
            return int(_numeric_prefix(value))
        case list() | dict():
            return 1 if value else 0
        case _:
            return 0


def php_float(text: str) -> float:
    """``(float)$text``: the numeric prefix, and 0 without one."""
    return _numeric_prefix(text)


def php_is_positive(value: object) -> bool:
    """``$value > 0``: an array is greater than any number, a boolean compares as a boolean."""
    match value:
        case bool():
            return value
        case int() | float():
            return value > 0
        case str():
            return _numeric_prefix(value) > 0
        case list() | dict():
            return True
        case _:
            return False


def _numeric_prefix(text: str) -> float:
    match = _NUMERIC_PREFIX.match(text)
    if match is None:
        return 0.0
    number = float(match.group())
    return number if math.isfinite(number) else 0.0


def _float_text(value: float) -> str:
    if not math.isfinite(value):
        return "NAN" if math.isnan(value) else ("-INF" if value < 0 else "INF")
    if value.is_integer() and abs(value) < 1e15:
        return str(int(value))
    return repr(float(f"{value:.14g}")).replace("e+", "E+").replace("e-", "E-")


def _decoded_entity(match: re.Match[str]) -> str:
    entity = match.group(1)
    if not entity.startswith("#"):
        code_point = html.entities.name2codepoint.get(entity)
        return chr(code_point) if code_point is not None else match.group()
    digits = entity[1:]
    code_point = int(digits[1:], 16) if digits[0] in "xX" else int(digits)
    return chr(code_point) if _is_allowed_code_point(code_point) else match.group()


def _is_allowed_code_point(code_point: int) -> bool:
    if code_point in (0x09, 0x0A, 0x0D):
        return True
    if code_point <= _MAX_CODE_POINT and code_point >= 0xE000:
        return True
    return 0x20 <= code_point <= 0x7E or 0xA0 <= code_point <= 0xD7FF


def _tag_end(text: str, start: int) -> int | None:
    quote = ""
    for index in range(start, len(text)):
        character = text[index]
        if quote:
            if character == quote:
                quote = ""
        elif character in "\"'":
            quote = character
        elif character == ">":
            return index
    return None


def _tag_name(tag: str) -> str:
    match = _TAG_NAME.match(tag)
    return _ascii_lower(match.group(1)) if match else ""


def _ascii_lower(text: str) -> str:
    return "".join(c.lower() if c.isascii() else c for c in text)
