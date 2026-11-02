"""What the view ``client-sign/sign-doc-list.phtml`` computes, and its rendering.

The templates come from the .phtml as they are: a line PHP ends with ``?>`` loses its newline,
which is what ``trim_blocks`` does, and the indentation before a tag stays, so whitespace matches.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

from src.shared.php_text import (
    php_casefold_equal,
    php_html_entity_decode,
    php_html_escape,
    php_int,
    php_is_positive,
    php_is_truthy,
    php_string,
    php_strip_tags,
    php_trim,
)

from .doc_types import AGREEMENT_MANUAL, DocumentType

_TEMPLATES = Path(__file__).parent / "templates"
_HEAD = (_TEMPLATES / "sign_doc_list_head.html").read_bytes()
OLD_SIGN_DOC_LIST_PAGE = (_TEMPLATES / "old_sign_doc_list.html").read_bytes()
_NO_NAME = "—"
_KEPT_DATE_TAGS = frozenset({"br"})

# PHP's patterns run on bytes: \s, \d and \b know only ASCII.
_WHITESPACE = re.compile(r"\s+", re.ASCII)
_IIN = re.compile(r"IIN(\d{12})", re.ASCII | re.IGNORECASE)
_BIN = re.compile(r"BIN(\d{12})", re.ASCII | re.IGNORECASE)
_CN = re.compile(r"\bCN\s*=\s*([^,]+)", re.ASCII | re.IGNORECASE)
_SN = re.compile(r"\bSN\s*=\s*([^,]+)", re.ASCII | re.IGNORECASE)
_GN = re.compile(r"\bGN\s*=\s*([^,]+)", re.ASCII | re.IGNORECASE)
_FIRST_PART = re.compile(r"^([^,]+),", re.ASCII)
_ONLY_IIN = re.compile(r"\d{12}", re.ASCII)


def _php_echo(value: object) -> object:
    return php_string(value) if value is None or isinstance(value, bool) else value


_environment = Environment(
    loader=FileSystemLoader(_TEMPLATES),
    # The page repeats PHP: each value is escaped, or not, where the .phtml does it.
    autoescape=False,  # noqa: S701
    trim_blocks=True,
    keep_trailing_newline=True,
    finalize=_php_echo,
)
_environment.filters["h"] = php_html_escape
_LIST_TEMPLATE = _environment.get_template("sign_doc_list.html.j2")
_ERROR_TEMPLATE = _environment.get_template("sign_doc_list_error.html.j2")


@dataclass(frozen=True)
class ParsedDn:
    """``cs_parse_dn``."""

    name: str = ""
    iin: str = ""
    bin: str = ""


@dataclass(frozen=True)
class SignatureView:
    shown_name: str
    iin: str
    date: object
    raw: str
    show_raw: bool
    bin: str = ""


@dataclass(frozen=True)
class DocumentCardView:
    document_type_name: object
    uuid: object
    doc_type: object
    download_type: str
    is_ds: bool
    is_agreement: bool
    is_manual: bool
    is_signed: bool
    show_sign_button: bool
    signature: SignatureView | None
    manager: SignatureView | None


@dataclass
class RevokedGroupView:
    document_type_name: object = None
    client_signs: list[SignatureView] = field(default_factory=list)
    manager_signs: list[SignatureView] = field(default_factory=list)


def render_sign_doc_list_error(error: str) -> bytes:
    return _HEAD + _ERROR_TEMPLATE.render(error=error).encode("utf-8", "surrogateescape")


def render_sign_doc_list(
    *,
    request_hash: str,
    client_info: dict[str, Any] | None,
    documents: list[dict[str, Any]],
    deleted_signatures: list[dict[str, Any]],
) -> bytes:
    page = _LIST_TEMPLATE.render(
        hash=request_hash,
        info=client_info or {},
        docs=[_document_card(document) for document in documents],
        revoked=_revoked_groups(deleted_signatures),
    )
    return _HEAD + page.encode("utf-8", "surrogateescape")


def parse_dn(raw_dn: object) -> ParsedDn:
    dn = php_strip_tags(php_html_entity_decode(php_string(raw_dn)))
    dn = dn.replace("\\", "").replace("\r", " ").replace("\n", " ")
    dn = _WHITESPACE.sub(" ", php_trim(dn))
    if not dn:
        return ParsedDn()
    surname = _matched(_SN, dn)
    given_name = _first_name(_common_name(dn), surname)
    patronymic = _matched(_GN, dn)
    return ParsedDn(
        name=" ".join(part for part in (surname, given_name, patronymic) if part),
        iin=_IIN.search(dn).group(1) if _IIN.search(dn) else "",
        bin=_BIN.search(dn).group(1) if _BIN.search(dn) else "",
    )


def _matched(pattern: re.Pattern[str], dn: str) -> str:
    match = pattern.search(dn)
    return php_trim(match.group(1)) if match else ""


def _common_name(dn: str) -> str:
    # With no explicit CN, everything before the first comma is the name.
    return _matched(_CN if _CN.search(dn) else _FIRST_PART, dn)


def _first_name(common_name: str, surname: str) -> str:
    if not common_name or _ONLY_IIN.fullmatch(common_name):
        return ""
    words = _WHITESPACE.split(common_name)
    words = [word for word in words if word]
    if not surname or len(words) < 2:
        return ""
    return next((word for word in words if not php_casefold_equal(word, surname)), "")


def _document_card(document: dict[str, Any]) -> DocumentCardView:
    doc_type = document.get("doc_type")
    is_ds = doc_type == DocumentType.DS
    is_manual = doc_type == AGREEMENT_MANUAL
    is_signed = php_is_truthy(document.get("signed"))
    return DocumentCardView(
        document_type_name=document.get("document_type_name"),
        uuid=document.get("uuid"),
        doc_type=doc_type,
        download_type=DocumentType.DS if is_ds else DocumentType.AGREEMENT,
        is_ds=is_ds,
        is_agreement=doc_type == DocumentType.AGREEMENT,
        is_manual=is_manual,
        is_signed=is_signed or is_manual,
        show_sign_button=bool(document.get("show_sign_buttons")) and not is_manual,
        signature=_client_signature(document) if is_signed and not is_manual else None,
        manager=_manager_signature(document),
    )


def _client_signature(document: dict[str, Any]) -> SignatureView:
    dn = php_string(document.get("dn_name"))
    parsed = parse_dn(dn)
    name = parsed.name or dn
    raw = php_trim(php_strip_tags(dn))
    date = php_strip_tags(php_string(document.get("sign_date_time")), _KEPT_DATE_TAGS)
    return SignatureView(
        shown_name=name if php_is_truthy(name) else _NO_NAME,
        iin=parsed.iin,
        date=date if php_is_truthy(date) else "",
        raw=raw,
        show_raw=php_is_truthy(raw) and raw != name,
    )


def _manager_signature(document: dict[str, Any]) -> SignatureView | None:
    if not php_is_positive(document.get("contract_signer_sign_id")):
        return None
    dn = php_string(document.get("contract_signer_dn_name"))
    parsed = parse_dn(dn)
    name = parsed.name or dn
    raw = php_trim(php_strip_tags(dn))
    return SignatureView(
        shown_name=name if php_is_truthy(name) else _NO_NAME,
        iin=parsed.iin,
        bin=parsed.bin,
        date=document.get("contract_signer_date_time"),
        raw=raw,
        show_raw=raw != "" and name != dn,
    )


def _revoked_groups(rows: list[dict[str, Any]]) -> list[RevokedGroupView]:
    groups: dict[str, RevokedGroupView] = {}
    for row in rows:
        group = groups.setdefault(php_string(row.get("group_code")), RevokedGroupView())
        if group.document_type_name is None:
            group.document_type_name = row.get("document_type_name")
        if php_int(row.get("sign_id")) > 0:
            group.client_signs.append(_revoked_signature(row["dn_name"], row["sign_date_time"]))
        if php_int(row.get("contract_signer_sign_id")) > 0:
            group.manager_signs.append(
                _revoked_signature(row["contract_signer_dn_name"], row["contract_signer_date_time"])
            )
    return list(groups.values())


def _revoked_signature(raw_dn: object, date: object) -> SignatureView:
    dn = php_string(raw_dn)
    parsed = parse_dn(dn)
    name = parsed.name or dn
    return SignatureView(
        shown_name=name,
        iin=parsed.iin,
        date=date,
        raw=dn,
        show_raw=dn != "" and dn != name,
    )
