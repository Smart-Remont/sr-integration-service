"""The ``.phtml`` view of letters. Values go in raw, as PHP prints them."""

from datetime import UTC, datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from src.php_compat.text import php_trim

_environment = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    # The views print HTML from the database as it is: that is the content of the letters.
    autoescape=False,  # noqa: S701
    trim_blocks=True,
    keep_trailing_newline=True,
    finalize=lambda value: "" if value is None else value,
)
_LETTER = _environment.get_template("email.html.j2")


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
        has_follow_button=bool(php_trim(follow_url)),
        follow_remont_btn=follow_url,
        follow_btn_text=follow_text,
        year=datetime.now(UTC).year,
    )
