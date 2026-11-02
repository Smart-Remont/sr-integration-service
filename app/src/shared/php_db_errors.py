"""Database errors as PHP saw them: the PDO message and the text of ``_getErrorClean``."""

import re
from itertools import accumulate
from unicodedata import east_asian_width

from asyncpg.exceptions import PostgresError

_RAISED_TEXT = re.compile(r"\{[^@]*\}")
_WORD_START = re.compile(r"(?<!^)(?=[A-Z])")
_UNHANDLED_PREFIX = "Произошла необработанная ошибка в БД "
# PDO names the state by its standard text; for these the asyncpg class name is not the same.
_STATE_NAMES = {"P0001": "Raise exception"}
_DISPLAY_WIDTH = 60
_MIN_RIGHT_CUT = 10


def php_error_text(exc: BaseException) -> str:
    return php_clean_error(php_pdo_message(exc))


def php_clean_error(pdo_message: str) -> str:
    """``_getErrorClean``: the raised ``{text}``, otherwise the PDO text behind a fixed prefix."""
    raised = _RAISED_TEXT.search(pdo_message)
    if raised:
        return raised.group().replace("{", "").replace("}", "")
    return f"{_UNHANDLED_PREFIX}{pdo_message}"


def php_pdo_message(exc: BaseException) -> str:
    """``PDOException::getMessage``: what libpq builds from the fields of the server error.

    The ``LINE`` of an error in the statement itself cannot be repeated: libpq prints it from the
    text of the query, and PHP sent other text than this service.
    """
    cause = _postgres_error(exc)
    if cause is None:
        return str(exc)
    # args[0], not str(): asyncpg adds DETAIL and HINT to str() on its own.
    lines = [
        cause.args[0],
        *_internal_query_cursor(cause),
        *_labelled("DETAIL", cause.detail),
        *_labelled("HINT", cause.hint),
        *_labelled("QUERY", cause.internal_query),
        *_labelled("CONTEXT", cause.context),
    ]
    return _pdo_message(cause.sqlstate, _state_name(cause), "\n".join(lines))


def is_php_unhandled_error(clean_text: str) -> bool:
    """A failure of the database itself, as opposed to a refusal a function raised on purpose."""
    return clean_text.startswith(_UNHANDLED_PREFIX)


def php_pdo_error_text(sqlstate: str, state_name: str, text: str) -> str:
    """The clean text of an error the database would give for a value checked before the call."""
    return f"{_UNHANDLED_PREFIX}{_pdo_message(sqlstate, state_name, text)}"


def php_invalid_encoding_message(text: str) -> str | None:
    """The PDO message for a parameter with invalid UTF-8, which asyncpg cannot even send."""
    raw = text.encode("utf-8", "surrogateescape")
    try:
        raw.decode()
    except UnicodeDecodeError as exc:
        length = _utf8_sequence_length(raw[exc.start])
        shown = " ".join(f"0x{byte:02x}" for byte in raw[exc.start : exc.start + length])
        return _pdo_message(
            "22021",
            "Character not in repertoire",
            f'invalid byte sequence for encoding "UTF8": {shown}',
        )
    return None


def _utf8_sequence_length(lead_byte: int) -> int:
    if 0xC0 <= lead_byte < 0xE0:
        return 2
    if 0xE0 <= lead_byte < 0xF0:
        return 3
    if 0xF0 <= lead_byte < 0xF8:
        return 4
    return 1


def _labelled(label: str, value: str | None) -> list[str]:
    return [f"{label}:  {value}"] if value else []


def _internal_query_cursor(error: PostgresError) -> list[str]:
    if not error.internal_query or not error.internal_position:
        return []
    return _cursor_lines(error.internal_query, int(error.internal_position))


def _cursor_lines(query: str, position: int) -> list[str]:
    """libpq ``reportErrorPosition``: the line with the error and a caret under its place."""
    location = position - 1
    if location < 0 or location > len(query):
        return []
    text = query.replace("\t", " ")
    columns = list(accumulate((_display_width(character) for character in text), initial=0))
    line_number, first, last = _error_line(text, location)
    first, last, cut_left, cut_right = _fit_to_display(columns, first, last, location)
    prefix = f"LINE {line_number}: {'...' if cut_left else ''}"
    shown = text[first:last] + ("..." if cut_right else "")
    caret = " " * (len(prefix) + columns[location] - columns[first])
    return [f"{prefix}{shown}", f"{caret}^"]


def _error_line(text: str, location: int) -> tuple[int, int, int]:
    line_number = 1
    first = 0
    for index, character in enumerate(text):
        if character not in "\r\n":
            continue
        if index >= location:
            return line_number, first, index
        if character == "\r" or index == 0 or text[index - 1] != "\r":
            line_number += 1
        first = index + 1
    return line_number, first, len(text)


def _fit_to_display(
    columns: list[int],
    first: int,
    last: int,
    location: int,
) -> tuple[int, int, bool, bool]:
    cut_left = cut_right = False
    if columns[last] - columns[first] <= _DISPLAY_WIDTH:
        return first, last, cut_left, cut_right
    if columns[first] + _DISPLAY_WIDTH >= columns[location] + _MIN_RIGHT_CUT:
        while columns[last] - columns[first] > _DISPLAY_WIDTH:
            last -= 1
        return first, last, cut_left, True
    while columns[location] + _MIN_RIGHT_CUT < columns[last]:
        last -= 1
        cut_right = True
    while columns[last] - columns[first] > _DISPLAY_WIDTH:
        first += 1
        cut_left = True
    return first, last, cut_left, cut_right


def _display_width(character: str) -> int:
    return 2 if east_asian_width(character) in ("W", "F") else 1


def _pdo_message(sqlstate: str, state_name: str, text: str) -> str:
    return f"SQLSTATE[{sqlstate}]: {state_name}: 7 ERROR:  {text}"


def _state_name(error: PostgresError) -> str:
    if error.sqlstate in _STATE_NAMES:
        return _STATE_NAMES[error.sqlstate]
    words = _WORD_START.sub(" ", type(error).__name__.removesuffix("Error"))
    return words.capitalize()


def _postgres_error(exc: BaseException) -> PostgresError | None:
    cause: BaseException | None = exc
    while cause is not None:
        if isinstance(cause, PostgresError):
            return cause
        cause = cause.__cause__
    return None
