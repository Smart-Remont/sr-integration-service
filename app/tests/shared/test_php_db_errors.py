"""Expected texts of the position lines were taken from ``psql``, which prints through libpq."""

from typing import Any

import pytest
from asyncpg.exceptions import PostgresError, RaiseError, UndefinedColumnError

from src.php_compat.pdo_errors import (
    is_php_unhandled_error,
    php_clean_error,
    php_error_text,
    php_pdo_message,
)


def _server_error(**fields: Any) -> PostgresError:
    """An exception built the way asyncpg builds it from the fields the server sends."""
    return PostgresError.new({"S": "ERROR", **fields})


def test_real_exception_does_not_repeat_detail_and_hint() -> None:
    error = _server_error(
        C="22007", M='invalid value "1-" for "DD"', D="The detail.", H="The hint."
    )
    assert "DETAIL" in str(error), "asyncpg adds the fields to str(): the reason for args[0]"

    assert php_pdo_message(error) == (
        'SQLSTATE[22007]: Invalid datetime format: 7 ERROR:  invalid value "1-" for "DD"\n'
        "DETAIL:  The detail.\n"
        "HINT:  The hint."
    )


def test_all_fields_come_in_the_order_of_libpq() -> None:
    error = _server_error(
        C="42703",
        M='column "x" does not exist',
        D="The detail.",
        H="The hint.",
        q="SELECT x",
        p="8",
        W="PL/pgSQL function f() line 3 at PERFORM",
    )

    assert php_pdo_message(error) == (
        'SQLSTATE[42703]: Undefined column: 7 ERROR:  column "x" does not exist\n'
        "LINE 1: SELECT x\n"
        "               ^\n"
        "DETAIL:  The detail.\n"
        "HINT:  The hint.\n"
        "QUERY:  SELECT x\n"
        "CONTEXT:  PL/pgSQL function f() line 3 at PERFORM"
    )


# Name, internal query, position and the two lines psql printed for them on PostgreSQL 12.
PSQL_CURSORS = [
    (
        "short",
        "SELECT x_missing",
        8,
        ["LINE 1: SELECT x_missing", "               ^"],
    ),
    (
        "long_error_at_start",
        "SELECT x_missing, aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, 1",
        8,
        [
            "LINE 1: SELECT x_missing, aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, aaaaaa...",
            "               ^",
        ],
    ),
    (
        "long_error_in_middle",
        "SELECT aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, x_missing, aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, 1",
        8,
        [
            "LINE 1: SELECT aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, aaaaa...",
            "               ^",
        ],
    ),
    (
        "long_error_at_end",
        "SELECT aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, x_missing",
        8,
        [
            "LINE 1: SELECT aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, aaaaa...",
            "               ^",
        ],
    ),
    (
        "multiline_lf",
        "SELECT 1,\n 2,\n x_missing,\n 4",
        16,
        ["LINE 3:  x_missing,", "         ^"],
    ),
    (
        "multiline_crlf",
        "SELECT 1,\r\n 2,\r\n x_missing",
        18,
        ["LINE 3:  x_missing", "         ^"],
    ),
    (
        "tab",
        "SELECT 1,\t x_missing",
        12,
        ["LINE 1: SELECT 1,  x_missing", "                   ^"],
    ),
    (
        "cyrillic_long",
        "SELECT 'длинная строка длинная строка длинная строка длинная строка длинная строка длинная строка длинная строка длинная строка ', x_missing, aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, 1",
        132,
        [
            "LINE 1: ...а длинная строка длинная строка длинная строка ', x_missing,...",
            "                                                             ^",
        ],
    ),
    (
        "wide",
        "SELECT '日本語日本語日本語日本語日本語日本語日本語日本語日本語日本語日本語日本語', x_missing, aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, aaaaaaaaaa, 1",
        48,
        [
            "LINE 1: ...本語日本語日本語日本語日本語日本語日本語日本語', x_missing,...",
            "                                                            ^",
        ],
    ),
]


@pytest.mark.parametrize(("name", "query", "position", "expected"), PSQL_CURSORS)
def test_cursor_lines_match_libpq(
    name: str,
    query: str,
    position: int,
    expected: list[str],
) -> None:
    error = _server_error(C="42703", M="m", q=query, p=str(position))

    lines = php_pdo_message(error).split("\n")

    assert lines[1:3] == expected, name


def test_cursor_is_left_out_without_a_position() -> None:
    error = UndefinedColumnError("m")

    assert php_pdo_message(error) == "SQLSTATE[42703]: Undefined column: 7 ERROR:  m"


def test_statement_position_is_not_printed() -> None:
    error = _server_error(C="42703", M="m", P="8")

    assert php_pdo_message(error) == "SQLSTATE[42703]: Undefined column: 7 ERROR:  m"


def test_raised_text_is_found_in_the_message() -> None:
    assert php_error_text(RaiseError("{Отказ}")) == "Отказ"
    assert not is_php_unhandled_error("Отказ")
    assert is_php_unhandled_error(php_clean_error("SQLSTATE[42703]: x"))


def test_pdo_state_name_of_a_too_long_value_has_its_comma() -> None:
    # pdo_sqlstate.c names it so, and Kaspi writes this text into its log row.
    error = _server_error(C="22001", M="value too long for type character varying(255)")

    assert php_pdo_message(error).startswith(
        "SQLSTATE[22001]: String data, right truncated: 7 ERROR:  value too long"
    )
