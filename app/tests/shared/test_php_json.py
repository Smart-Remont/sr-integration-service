from datetime import UTC, date, datetime, time
from decimal import Decimal
from uuid import UUID

import pytest

from src.shared.php_json import pg_row, php_json_decode, php_json_encode, php_json_encode_pretty


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("11871723"), "11871723"),
        (Decimal("33.130"), "33.130"),
        (0.0, "0"),
        (33.13, "33.13"),
        (1e15, "1e+15"),
        (datetime(2026, 9, 9, 9, 1, 44), "2026-09-09 09:01:44"),
        (datetime(2022, 7, 20, 15, 45, 37, 476000), "2022-07-20 15:45:37.476"),
        (datetime(2026, 9, 9, 4, 1, 44, tzinfo=UTC), "2026-09-09 09:01:44+05"),
        (date(2025, 12, 30), "2025-12-30"),
        (time(9, 5, 1), "09:05:01"),
        (UUID("c0f17482-ca65-4e58-8446-d1b155f45945"), "c0f17482-ca65-4e58-8446-d1b155f45945"),
        ([1, 2], "{1,2}"),
        (["a b", None, 'x"y', True], '{"a b",NULL,"x\\"y",t}'),
    ],
)
def test_row_values_become_postgres_text(value: object, expected: str) -> None:
    assert pg_row({"column": value}) == {"column": expected}


@pytest.mark.parametrize("value", [428, True, False, None, "текст"])
def test_integers_booleans_and_text_stay_native(value: object) -> None:
    assert pg_row({"column": value})["column"] is value


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (b'{"a": 1.5}', {"a": 1.5}),
        (b"{}", []),
        (b'{"a": {}, "b": {"0": "x", "1": "y"}}', {"a": [], "b": ["x", "y"]}),
        (b"{broken", None),
        (b"", None),
        (b"NaN", None),
        (b"\xff", None),
    ],
)
def test_decode_follows_php_arrays(text: bytes, expected: object) -> None:
    assert php_json_decode(text) == expected


def test_encode_escapes_slashes_but_not_cyrillic() -> None:
    assert (
        php_json_encode({"a": "б/в", "n": [], "f": 1.5}) == '{"a":"б\\/в","n":[],"f":1.5}'.encode()
    )


def test_encode_escapes_line_separators() -> None:
    assert php_json_encode("a b") == b'"a\\u2028b"'


def test_pretty_keeps_slashes_and_empty_containers() -> None:
    assert php_json_encode_pretty({"a": "б/в", "d": {}}) == (
        '{\n    "a": "б/в",\n    "d": {}\n}'.encode()
    )
