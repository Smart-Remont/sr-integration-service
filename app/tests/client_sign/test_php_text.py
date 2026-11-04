import pytest

from src.features.client_sign.views import ParsedDn, parse_dn
from src.php_compat.text import (
    php_casefold_equal,
    php_html_entity_decode,
    php_html_escape,
    php_int,
    php_is_positive,
    php_is_truthy,
    php_string,
    php_strip_tags,
    php_strtoupper,
)


def test_html_escape_keeps_single_quotes_and_empties_invalid_utf8() -> None:
    assert php_html_escape("""a&b<c>"d'""") == "a&amp;b&lt;c&gt;&quot;d'"
    assert php_html_escape(None) == ""
    assert php_html_escape("a\udcff") == ""


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("a<br/>b", "ab"),
        ("a < b", "a < b"),
        ("<b>x</b>", "x"),
        ('<a title=">">x</a>', "x"),
        ("a<b", "a"),
        ("x>y", "x>y"),
    ],
)
def test_strip_tags(text: str, expected: str) -> None:
    assert php_strip_tags(text) == expected


def test_strip_tags_keeps_allowed_in_both_forms() -> None:
    allowed = frozenset({"br"})
    assert php_strip_tags("a<br/>b<br>c<b>d</b>", allowed) == "a<br/>b<br>cd"


def test_entity_decode_needs_a_semicolon_and_the_html4_table() -> None:
    assert php_html_entity_decode("&quot;x&#039;&#x41;&amp;") == "\"x'A&"
    assert (
        php_html_entity_decode("&quot x &apos; &#0; &unknown;") == "&quot x &apos; &#0; &unknown;"
    )


def test_strtoupper_and_compare_leave_cyrillic_alone() -> None:
    assert php_strtoupper("ds-ф") == "DS-ф"
    assert php_casefold_equal("IvAn", "iVaN")
    assert not php_casefold_equal("ИВАН", "иван")


@pytest.mark.parametrize(
    ("value", "expected"),
    [("12abc", 12), ("abc", 0), (" 7", 7), ("1e3", 1000), (1.9, 1), (True, 1), ([], 0), ([1], 1)],
)
def test_php_int(value: object, expected: int) -> None:
    assert php_int(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, False), ("abc", False), ("5", True), (0, False), (3, True), ([], True), (True, True)],
)
def test_php_is_positive(value: object, expected: bool) -> None:
    assert php_is_positive(value) is expected


def test_php_string_and_truthy() -> None:
    assert [php_string(v) for v in (None, False, True, 5, 1.5, [1], {"a": 1}, "x")] == [
        "",
        "",
        "1",
        "5",
        "1.5",
        "Array",
        "Array",
        "x",
    ]
    assert not php_is_truthy("0")
    assert not php_is_truthy("")
    assert php_is_truthy("00")
    assert not php_is_truthy(b"0")
    assert not php_is_truthy(b"")
    assert php_is_truthy(b"%PDF")


def test_parse_dn_builds_surname_name_patronymic() -> None:
    parsed = parse_dn("CN=ИВАНОВ ИВАН,SN=ИВАНОВ,GN=ИВАНОВИЧ,C=KZ,IIN123456789012,BIN987654321098")
    assert parsed == ParsedDn(name="ИВАНОВ ИВАН ИВАНОВИЧ", iin="123456789012", bin="987654321098")


def test_parse_dn_without_explicit_cn_takes_text_before_the_comma() -> None:
    assert parse_dn("ИВАНОВ ИВАН, SN=ИВАНОВ").name == "ИВАНОВ ИВАН"


def test_parse_dn_ignores_a_cn_that_is_an_iin_and_cleans_markup() -> None:
    assert parse_dn("CN=123456789012,SN=ИВАНОВ").name == "ИВАНОВ"
    assert parse_dn("").name == ""
    assert parse_dn('a\\"b<br/>, IIN123456789012').iin == "123456789012"
