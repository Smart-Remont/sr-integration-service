"""XML exactly as PHP ``DOMDocument`` with ``formatOutput`` writes it (libxml2).

``createElement($name, $value)`` does not escape ``$value``: libxml2 reads entities in it, so
``&lt;`` becomes a character, ``&foo;`` stays a reference and a lone ``&`` empties the element.
Everything works on bytes because PHP passes invalid UTF-8 through to the response.
"""

from dataclasses import dataclass

_DECLARATION = b'<?xml version="1.0" encoding="UTF-8"?>\n'
_PREDEFINED_ENTITIES = {b"lt": b"<", b"gt": b">", b"amp": b"&", b"quot": b'"', b"apos": b"'"}
_UNICODE_LIMIT = 0x110000
_INDENT = b"  "


@dataclass(frozen=True)
class DomElement:
    name: str
    text: str | None = None
    attributes: tuple[tuple[str, str], ...] = ()
    children: tuple[DomElement, ...] = ()


def dom_save_xml(root: DomElement) -> bytes:
    return _DECLARATION + _element_bytes(root, depth=0) + b"\n"


def _element_bytes(element: DomElement, *, depth: int) -> bytes:
    tag = element.name.encode()
    attributes = b"".join(
        b' %b="%b"' % (name.encode(), value.encode()) for name, value in element.attributes
    )
    if element.children:
        indent = _INDENT * depth
        inner = b"".join(
            indent + _INDENT + _element_bytes(child, depth=depth + 1) + b"\n"
            for child in element.children
        )
        return b"<%b%b>\n%b%b</%b>" % (tag, attributes, inner, indent, tag)
    content = _element_content(element.text or "")
    if not content:
        return b"<%b%b/>" % (tag, attributes)
    return b"<%b%b>%b</%b>" % (tag, attributes, content, tag)


def _element_content(text: str) -> bytes:
    """``xmlStringGetNodeList`` and then the writer of the nodes it made."""
    # PHP hands libxml2 a C string, which ends at the first NUL byte.
    value = text.encode("utf-8", "surrogateescape").partition(b"\0")[0]
    written = pending = b""
    position = start = 0
    while (ampersand := value.find(b"&", position)) >= 0:
        pending += value[start:ampersand]
        if value[ampersand + 1 : ampersand + 2] == b"#":
            character, position = _char_reference(value, ampersand)
            pending += character
        else:
            end = value.find(b";", ampersand + 1)
            if end < 0:
                # libxml2 gives up on an unterminated entity and drops the text it still holds.
                return written
            name = value[ampersand + 1 : end]
            if name in _PREDEFINED_ENTITIES:
                pending += _PREDEFINED_ENTITIES[name]
            elif name:
                written += _escape(pending) + b"&" + name + b";"
                pending = b""
            position = end + 1
        start = position
    return written + _escape(pending + value[start:])


def _char_reference(value: bytes, ampersand: int) -> tuple[bytes, int]:
    is_hexadecimal = value[ampersand + 2 : ampersand + 3] == b"x"
    base = 16 if is_hexadecimal else 10
    position = ampersand + (3 if is_hexadecimal else 2)
    code = 0
    while (byte := value[position : position + 1]) != b";":
        digit = _digit(byte, base)
        if digit is None:
            return b"", position
        code = min(code * base + digit, _UNICODE_LIMIT)
        position += 1
    is_writable = 0 < code < _UNICODE_LIMIT
    return (chr(code).encode("utf-8", "surrogatepass") if is_writable else b""), position + 1


def _digit(byte: bytes, base: int) -> int | None:
    try:
        return int(byte.decode("ascii"), base) if byte.isalnum() else None
    except ValueError:
        return None


def _escape(text: bytes) -> bytes:
    return (
        text.replace(b"&", b"&amp;")
        .replace(b"<", b"&lt;")
        .replace(b">", b"&gt;")
        .replace(b"\r", b"&#13;")
    )
