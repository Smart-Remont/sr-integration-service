"""The composition of letters, against bytes taken from ``Zend_Mail`` in the contour."""

import re
from datetime import UTC, datetime

import pytest

from src.features.mailing.message import (
    MailAttachment,
    MailCompositionError,
    compose_letter,
    encode_quoted_printable,
)

NOW = datetime(2026, 11, 3, 10, 20, 30, tzinfo=UTC)


def _letter(**fields: object) -> bytes:
    values: dict[str, object] = {
        "from_address": "from@test.invalid",
        "from_name": "Name",
        "to": "to@test.invalid",
        "subject": "Subject",
        "html": "<p>x</p>",
        "now": NOW,
        **fields,
    }
    return compose_letter(**values).content  # type: ignore[arg-type]


def test_plain_letter_in_the_order_of_zend() -> None:
    assert _letter(extra_headers=[("Precedence", "list")]) == (
        b"Precedence: list\n"
        b"From: Name <from@test.invalid>\n"
        b"To: to@test.invalid\n"
        b"Subject: Subject\n"
        b"Date: Tue, 03 Nov 2026 10:20:30 +0000\n"
        b"Content-Type: text/html; charset=utf-8\n"
        b"Content-Transfer-Encoding: quoted-printable\n"
        b"Content-Disposition: inline\n"
        b"MIME-Version: 1.0\n"
        b"\n"
        b"<p>x</p>"
    )


def test_cyrillic_subject_is_encoded_with_the_label_of_zend() -> None:
    letter = _letter(subject="Тема письма")

    assert b"Subject: =?utf8?Q?=D0=A2=D0=B5=D0=BC=D0=B0=20?=\n =?utf8?Q?" not in letter
    assert b"Subject: =?utf8?Q?=D0=A2=D0=B5=D0=BC=D0=B0=20=D0=BF" in letter


def test_long_subject_breaks_only_at_an_encoded_space() -> None:
    letter = _letter(subject="слово " * 30)

    subject = letter.split(b"Subject: ", 1)[1].split(b"\nDate", 1)[0]
    lines = subject.split(b"\n")
    assert len(lines) > 1
    assert all(line.strip().startswith(b"=?utf8?Q?") for line in lines)


def test_header_values_are_cleaned_as_in_zend() -> None:
    letter = _letter(
        to='a"b<c>,d\r\n@test.invalid',
        subject="a\tb\r\nc",
        from_name='Na"me <x>',
    )

    assert b"To: abcd@test.invalid\n" in letter
    assert b"Subject: abc\n" in letter
    assert b'From: "Na\'me [x]" <from@test.invalid>\n' in letter


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("", b"From: from@test.invalid\n"),
        ("from@test.invalid", b"From: from@test.invalid\n"),
        ("A, B", b'From: "A, B" <from@test.invalid>\n'),
    ],
)
def test_sender_forms(name: str, expected: bytes) -> None:
    assert expected in _letter(from_name=name)


def test_too_long_header_line_fails_as_in_zend() -> None:
    with pytest.raises(MailCompositionError, match="At least one mail header line is too long"):
        _letter(subject="x" * 1200)


def test_attachments_make_it_multipart_with_the_body_headers_on_top() -> None:
    letter = compose_letter(
        from_address="from@test.invalid",
        from_name="Name",
        to="to@test.invalid",
        subject="Subject",
        html="<p>x</p>",
        attachments=[MailAttachment("Файл.pdf", b"abc"), MailAttachment(None, b"")],
        now=NOW,
    ).content
    boundary = re.search(rb'boundary="([^"]+)"', letter).group(1)

    assert re.fullmatch(rb"=_[0-9a-f]{32}", boundary)
    assert letter.replace(boundary, b"B") == (
        b"From: Name <from@test.invalid>\n"
        b"To: to@test.invalid\n"
        b"Subject: Subject\n"
        b"Date: Tue, 03 Nov 2026 10:20:30 +0000\n"
        b'Content-Type: multipart/mixed;\n boundary="B"\n'
        b"Content-Transfer-Encoding: quoted-printable\n"
        b"Content-Disposition: inline\n"
        b"MIME-Version: 1.0\n"
        b"\n"
        b"This is a message in Mime Format.  If you see this, your mail reader does not support "
        b"this format.\n"
        b"\n--B\n"
        b"Content-Type: text/html; charset=utf-8\n"
        b"Content-Transfer-Encoding: quoted-printable\n"
        b"Content-Disposition: inline\n"
        b"\n"
        b"<p>x</p>"
        b"\n--B\n"
        b"Content-Type: application/pdf\n"
        b"Content-Transfer-Encoding: base64\n"
        b'Content-Disposition: attachment; filename="' + "Файл.pdf".encode() + b'"\n'
        b"\n"
        b"YWJj"
        b"\n--B\n"
        b"Content-Type: application/pdf\n"
        b"Content-Transfer-Encoding: base64\n"
        b"Content-Disposition: attachment\n"
        b"\n"
        b"\n--B--"
    )


def test_copy_goes_after_the_addressee_and_is_not_doubled() -> None:
    letter = compose_letter(
        from_address="from@test.invalid",
        from_name="Name",
        to="to@test.invalid",
        subject="Subject",
        html="x",
        carbon_copy=("to@test.invalid", "Копия"),
    )

    assert letter.recipients == ("to@test.invalid",)
    assert b"Cc: =?utf8?Q?=D0=9A=D0=BE=D0=BF=D0=B8=D1=8F?= <to@test.invalid>\n" in letter.content


@pytest.mark.parametrize(
    ("raw", "encoded"),
    [
        (b"", b""),
        (b"a=b", b"a=3Db"),
        (b"text   ", b"text"),
        (b"a\tb\r\n", b"a=09b=0D=0A"),
        ("я".encode(), b"=D1=8F"),
        (b"0", b""),
        (b"a" * 72 + b"0", b"a" * 72),
        (b"a" * 80, b"a" * 72 + b"=\n" + b"a" * 8),
        (b"a" * 71 + b"=", b"a" * 71 + b"=\n=3D"),
        (b"a" * 71 + b" b", b"a" * 71 + b"=\n b"),
    ],
)
def test_quoted_printable_as_zend_mime(raw: bytes, encoded: bytes) -> None:
    assert encode_quoted_printable(raw) == encoded
