"""A letter byte for byte as ``Zend_Mail`` and ``Zend_Mail_Transport_Abstract`` compose it.

``email.message`` folds and encodes differently, and a letter would then differ from what PHP sends
in every comparison. Odd parts are kept on purpose, mail readers have been seeing them for years: the
label ``utf8`` in an encoded word, and the headers of the body repeated at the top of a letter with
attachments.
"""

import base64
import hashlib
import re
import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

_LINE_END = b"\n"
_LINE_LENGTH = 72
_HEADER_LIMIT = 998
_CHARSET = "utf8"
_PHP_TRIM = b" \t\n\r\0\x0b"
_PHP_TRIM_TEXT = " \t\n\r\0\x0b"
_ADDRESS_RULE = str.maketrans({"\r": "", "\n": "", "\t": "", '"': "", ",": "", "<": "", ">": ""})
_NAME_RULE = str.maketrans({"\r": "", "\n": "", "\t": "", '"': "'", "<": "[", ">": "]"})
_OTHER_RULE = str.maketrans({"\r": "", "\n": "", "\t": ""})
_NOT_PRINTABLE = re.compile(rb"[\x00-\x1f\x7f-\xff]")
_NAME_SPECIALS = re.compile(r"[()<>\[\]:;@\\,]")
_BODY_HEADERS = {
    "Content-Type": "text/html; charset=utf-8",
    "Content-Transfer-Encoding": "quoted-printable",
    "Content-Disposition": "inline",
}
_MULTIPART_PREAMBLE = (
    b"This is a message in Mime Format.  If you see this, "
    b"your mail reader does not support this format.\n"
)


class MailCompositionError(Exception):
    """``Zend_Mail_Exception``: PHP treats it as a failed sending, with the code 0."""


@dataclass(frozen=True)
class MailAttachment:
    filename: str | None
    content: bytes


@dataclass(frozen=True)
class ComposedLetter:
    return_path: str
    recipients: tuple[str, ...]
    content: bytes


def compose_letter(
    *,
    from_address: str,
    from_name: str,
    to: Sequence[str],
    subject: str | None,
    html: str,
    attachments: Sequence[MailAttachment] = (),
    carbon_copy: tuple[str, str] | None = None,
    extra_headers: Sequence[tuple[str, str]] = (),
    now: datetime | None = None,
) -> ComposedLetter:
    sender = _filter_address(from_address)
    addressees = [_filter_address(item) for item in to]
    recipients = dict.fromkeys(addressees)
    headers = {name: _encode_header(_filter_other(value)) for name, value in extra_headers}
    headers["From"] = _format_address(sender, from_name)
    if addressees:
        headers["To"] = ",\n ".join(addressees)
    if carbon_copy is not None:
        address = _filter_address(carbon_copy[0])
        headers["Cc"] = _format_address(address, carbon_copy[1])
        recipients[address] = None
    headers["Subject"] = _encode_header(_filter_other(subject or ""))
    headers["Date"] = (now or datetime.now(UTC)).strftime("%a, %d %b %Y %H:%M:%S +0000")
    headers |= _BODY_HEADERS
    html_text = encode_quoted_printable(html.encode("utf-8", "surrogateescape"))
    if attachments:
        boundary = _new_boundary()
        headers["Content-Type"] = f'multipart/mixed;\n boundary="{boundary}"'
        body = _multipart_body(boundary, html_text, attachments)
    else:
        body = html_text.strip(_PHP_TRIM)
    headers["MIME-Version"] = "1.0"
    head = "".join(f"{name}: {value}\n" for name, value in headers.items()).encode(
        "utf-8", "surrogateescape"
    )
    if any(len(line.strip(_PHP_TRIM)) > _HEADER_LIMIT for line in head.split(_LINE_END)):
        raise MailCompositionError("At least one mail header line is too long")
    return ComposedLetter(
        return_path=sender,
        recipients=tuple(recipients),
        content=head + _LINE_END + body,
    )


def encode_quoted_printable(data: bytes) -> bytes:
    """``Zend_Mime::encodeQuotedPrintable``, with its loop: a remaining ``0`` is false in PHP."""
    remaining = _quoted_printable_text(data)
    lines = bytearray()
    while remaining and remaining != b"0":
        end = min(len(remaining), _LINE_LENGTH)
        position = remaining.rfind(b"=", 0, end)
        if position != -1 and position >= end - 2:
            end = position
        if end > 0 and remaining[end - 1 : end] == b" ":
            end -= 1
        lines += remaining[:end] + b"=" + _LINE_END
        remaining = remaining[end:]
    return bytes(lines).rstrip(_LINE_END).rstrip(b"=")


def _quoted_printable_text(data: bytes) -> bytes:
    text = data.replace(b"=", b"=3D")
    return _NOT_PRINTABLE.sub(lambda m: b"=%02X" % m.group(0)[0], text).rstrip(_PHP_TRIM)


def _encode_header(value: str) -> str:
    data = value.encode("utf-8", "surrogateescape")
    if not _NOT_PRINTABLE.search(data):
        return value
    return _encode_quoted_printable_header(data)


def _encode_quoted_printable_header(data: bytes) -> str:
    """``Zend_Mime::encodeQuotedPrintableHeader``: a line ends only at an encoded space."""
    prefix = f"=?{_CHARSET}?Q?".encode()
    limit = _LINE_LENGTH - len(prefix) - 3
    text = (
        _quoted_printable_text(data)
        .replace(b"?", b"=3F")
        .replace(b" ", b"=20")
        .replace(b"_", b"=5F")
    )
    lines = [b""]
    pending = b""
    position = 0
    while position < len(text):
        size = 3 if text[position : position + 1] == b"=" else 1
        token = text[position : position + size]
        position += size
        pending += token
        if token == b"=20":
            if len(lines[-1] + pending) > limit:
                lines.append(pending)
            else:
                lines[-1] += pending
            pending = b""
        if position >= len(text):
            lines[-1] += pending
    joined = _LINE_END.join(b" " + prefix + line + b"?=" for line in lines)
    return joined.strip(_PHP_TRIM).decode("ascii")


def _format_address(address: str, name: str) -> str:
    cleaned = name.translate(_NAME_RULE).strip(_PHP_TRIM_TEXT)
    if cleaned in ("", address):
        return address
    encoded = _encode_header(cleaned)
    if encoded == cleaned and _NAME_SPECIALS.search(cleaned):
        return f'"{encoded}" <{address}>'
    return f"{encoded} <{address}>"


def _filter_address(address: str) -> str:
    return address.translate(_ADDRESS_RULE)


def _filter_other(value: str) -> str:
    return value.translate(_OTHER_RULE)


def _attachment_part(attachment: MailAttachment) -> bytes:
    disposition = "attachment"
    # ``if ($this->filename)``: a null, an empty name and "0" give no file name.
    if attachment.filename and attachment.filename != "0":
        disposition += f'; filename="{attachment.filename}"'
    encoded = base64.b64encode(attachment.content)
    lines = [encoded[i : i + _LINE_LENGTH] for i in range(0, len(encoded), _LINE_LENGTH)]
    return (
        b"Content-Type: application/pdf\n"
        b"Content-Transfer-Encoding: base64\n"
        b"Content-Disposition: " + disposition.encode("utf-8", "surrogateescape") + b"\n"
        b"\n" + _LINE_END.join(lines)
    )


def _multipart_body(
    boundary: str,
    html_text: bytes,
    attachments: Sequence[MailAttachment],
) -> bytes:
    line = _LINE_END + b"--" + boundary.encode() + _LINE_END
    html_part = (
        "".join(f"{name}: {value}\n" for name, value in _BODY_HEADERS.items()).encode()
        + _LINE_END
        + html_text
    )
    parts = [html_part, *(_attachment_part(item) for item in attachments)]
    content = _MULTIPART_PREAMBLE + b"".join(line + part for part in parts)
    content += _LINE_END + b"--" + boundary.encode() + b"--" + _LINE_END
    return content.strip(_PHP_TRIM)


def _new_boundary() -> str:
    return "=_" + hashlib.md5(secrets.token_bytes(16), usedforsecurity=False).hexdigest()
