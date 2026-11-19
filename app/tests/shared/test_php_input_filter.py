"""``filter_arr`` and ``(string)$float``. Expected values are from PHP 7.4.3 in the closed contour."""

import pytest

from src.php_compat.input_filter import php_filter_text, php_filter_values
from src.php_compat.text import php_string


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Тест", "Тест"),
        ("  Тест  ", "Тест"),
        ("Тест     Тест", "Тест Тест"),
        ("Тест\tТест\nТест", "Тест Тест Тест"),
        ("Тест Тест", "Тест Тест"),
        (" ", ""),
        ("<b>\"Тест\" & 'Ко'</b>", "&lt;b&gt;&quot;Тест&quot; &amp; 'Ко'&lt;/b&gt;"),
        ("&amp;", "&amp;amp;"),
        ("Те\u0000ст", "Тест"),
        ("Те\u0001ст", "Те ст"),
        ("Те​ст", "Те ст"),
        ("Тй", "Ти"),
        ("Тест😀", "Тест"),
        ("Тест🌍🚀☀✂", "Тест"),
        ("Тест🤖", "Тест🤖"),
        ("𝐀", "𝐀"),
    ],
)
def test_filter_text(value: str, expected: str) -> None:
    assert php_filter_text(value) == expected


def test_a_with_circumflex_is_replaced_too() -> None:
    """``[\\xC2\\xA0]`` under /u is two code points, the bytes of NBSP were meant."""
    assert php_filter_text("Âa âb") == "a âb"


def test_code_points_newer_than_pcre_are_replaced_and_older_ones_stay() -> None:
    assert php_filter_text("Те\U0001fae0ст") == "Те ст"
    assert php_filter_text("ТеᲉст") == "Те ст"
    assert php_filter_text("Теᲈст") == "Теᲈст"


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, ""), (True, "1"), (False, ""), (5, "5"), (1.5, "1.5"), (1.0, "1"), (1e25, "1.0E+25")],
)
def test_filter_turns_every_scalar_into_text(value: object, expected: str) -> None:
    assert php_filter_text(value) == expected


def test_filter_values_walks_values_and_keeps_keys() -> None:
    value = {"<k>": "<v>", "list": [1, None, {"a": " x "}], "empty": []}
    assert php_filter_values(value) == {
        "<k>": "&lt;v&gt;",
        "list": ["1", "", {"a": "x"}],
        "empty": [],
    }


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1e14, "1.0E+14"),
        (1e13, "10000000000000"),
        (123456789012345.0, "1.2345678901234E+14"),
        (1e-5, "1.0E-5"),
        (0.0001, "0.0001"),
        (1e25, "1.0E+25"),
        (1.5e300, "1.5E+300"),
        (-1e-10, "-1.0E-10"),
        (0.1 + 0.2, "0.3"),
        (-0.0, "-0"),
        (0.0, "0"),
        (float("inf"), "INF"),
        (float("-inf"), "-INF"),
    ],
)
def test_float_text_with_precision_14(value: float, expected: str) -> None:
    assert php_string(value) == expected
