"""``filter_arr``: what PHP did to every value of a lead before it reached the database."""

import re
import unicodedata

from .pcre_unicode import UNASSIGNED_IN_PCRE
from .text import php_html_escape, php_string, php_trim

# ``remove_emoji``: only these blocks. Newer emoji fall to ``_ReplacedBySpace`` below.
_EMOJI = re.compile(
    "[\U0001f600-\U0001f64f\U0001f300-\U0001f5ff\U0001f680-\U0001f6ff\u2600-\u27bf]"
)
_KEPT_CATEGORIES = frozenset("LNPSZ")
_SPACE = 0x20
# ``[\xC2\xA0]`` under the /u flag means two code points: U+00C2 is a real letter (Â) and goes too.
_ALSO_REPLACED = frozenset((0xC2, 0xA0))


class _ReplacedBySpace(dict[int, int]):
    r"""A table for ``str.translate``: ``/[^\pL\pN\pP\pS\pZ]|[\xC2\xA0]/u``. Filled on first use."""

    def __missing__(self, code_point: int) -> int:
        replaced = (
            code_point in _ALSO_REPLACED
            or unicodedata.category(chr(code_point))[0] not in _KEPT_CATEGORIES
            or any(first <= code_point <= last for first, last in UNASSIGNED_IN_PCRE)
        )
        result = _SPACE if replaced else code_point
        self[code_point] = result
        return result


_REPLACED_BY_SPACE = _ReplacedBySpace()


def php_filter_text(value: object) -> str:
    """``stripWhitespaces(htmlspecialchars(remove_emoji($value)))``, so numbers come out as text."""
    text = php_html_escape(_EMOJI.sub("", php_string(value)))
    while True:
        # htmlspecialchars left no "<", so all strip_tags does here is drop NUL bytes.
        cleaned = text.replace("\0", "").translate(_REPLACED_BY_SPACE).replace("  ", " ")
        cleaned = php_trim(cleaned)
        if cleaned == text:
            return text
        text = cleaned


def php_filter_values[T](array: T) -> T:
    """``array_walk_recursive($array, "filter_arr")``: values change, keys stay as they are."""
    if isinstance(array, list):
        return [php_filter_values(item) for item in array]
    if isinstance(array, dict):
        return {key: php_filter_values(item) for key, item in array.items()}
    return php_filter_text(array)
