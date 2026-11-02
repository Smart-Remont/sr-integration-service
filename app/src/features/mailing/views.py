"""The ``.phtml`` views of letters and of the provider tables. Values go in raw, as PHP prints them."""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from math import ceil, floor, isfinite, log10
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

_PHP_WHITESPACE = " \t\n\r\0\x0b"


def _round_half_away(value: float) -> float:
    return float(floor(value + 0.5)) if value >= 0 else float(ceil(value - 0.5))


def _php_round(value: float, places: int) -> float:
    """``_php_math_round`` of PHP 7.4, with its rounding to 15 digits first: 1.005 gives 1.01."""
    if not isfinite(value) or value == 0.0:
        return value
    precision = 14 - floor(log10(abs(value)))
    factor = 10.0**places
    if places < precision < places + 15:
        tmp = _round_half_away(
            value * 10.0**precision if precision >= 0 else value / 10.0**-precision
        )
        tmp /= 10.0 ** (precision - places)
    else:
        tmp = value * factor
        if abs(tmp) >= 1e15:
            return value
    return _round_half_away(tmp) / factor


def tenge_text(number: object) -> str:
    """``tenge_text``: a whole sum without kopecks, spaces between thousands."""
    if number is None or number == "":
        return ""
    value = float(str(number))
    if value == _php_round(value, 0):
        return f"{_php_round(value, 0):,.0f}".replace(",", " ")
    return f"{_php_round(value, 2):,.2f}".replace(",", " ")


def _status_lines(statuses: object) -> str:
    return "" if statuses is None else str(statuses).replace("</span>", "</span><br>")


_environment = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    # The views print HTML from the database as it is: that is the content of the letters.
    autoescape=False,  # noqa: S701
    trim_blocks=True,
    keep_trailing_newline=True,
    finalize=lambda value: "" if value is None else value,
)
_environment.filters["tenge_text"] = tenge_text
_environment.filters["status_lines"] = _status_lines
_LETTER = _environment.get_template("email.html.j2")
_PROVIDER_TABLE = _environment.get_template("provider_request_unload.html.j2")


def render_letter(
    *,
    email_content: object,
    unsubscribe_url: str,
    employee_fio: object,
    follow_url: str,
    follow_text: str,
) -> str:
    return _LETTER.render(
        email_content=email_content,
        unsubscribe_url=unsubscribe_url,
        employee_fio=employee_fio,
        has_follow_button=bool(follow_url.strip(_PHP_WHITESPACE)),
        follow_remont_btn=follow_url,
        follow_btn_text=follow_text,
        year=datetime.now(UTC).year,
    )


def render_provider_table(
    *,
    provider_name: object,
    status_name: str,
    rows: Sequence[Mapping[str, object]],
) -> str:
    """Empty without rows: ``provider-request-unload.phtml`` returns before printing anything."""
    if not rows:
        return ""
    return _PROVIDER_TABLE.render(provider_name=provider_name, status_name=status_name, rows=rows)
