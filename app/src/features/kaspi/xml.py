"""Kaspi answers. The order of elements and every empty element are as in PHP."""

from src.repository.base import SpRow
from src.shared.php_xml import DomElement, dom_save_xml

_FIELDS = (
    ("field1", "prop_fio", "ФИО"),
    ("field2", "prop_number", "Номер договора"),
    ("field3", "prop_date", "Дата договора"),
    ("field4", "resident_name", "ЖК"),
    ("field5", "company_name_official", "Название компании"),
)


def error_xml(txn_id: str, code: int, comment: str | None = None) -> bytes:
    """``getErrorXml``: an empty ``txn_id`` or comment is left out, ``0`` is not."""
    elements = [
        *([_element("txn_id", txn_id)] if txn_id else []),
        _element("result", code),
        *([_element("comment", comment)] if comment else []),
    ]
    return _response(elements)


def check_xml(txn_id: str, result: int | None, info: SpRow | None) -> bytes:
    row = info or {}
    fields = tuple(
        DomElement(element, _text(row.get(column)), attributes=(("name", title),))
        for element, column, title in _FIELDS
    )
    return _response(
        [
            _element("txn_id", txn_id),
            _element("result", result),
            _element("bin", row.get("company_bin")),
            DomElement("fields", children=fields),
            _element("comment", ""),
        ],
    )


def pay_xml(
    *,
    txn_id: str,
    provider_txn: int | None,
    amount: str,
    result: int | None,
    company_bin: object,
) -> bytes:
    return _response(
        [
            _element("txn_id", txn_id),
            _element("prv_txn", provider_txn),
            _element("sum", amount),
            _element("result", result),
            _element("bin", company_bin),
            _element("comment", "OK"),
        ],
    )


def _response(elements: list[DomElement]) -> bytes:
    return dom_save_xml(DomElement("response", children=tuple(elements)))


def _element(name: str, value: object) -> DomElement:
    return DomElement(name, _text(value))


def _text(value: object) -> str | None:
    return None if value is None else str(value)
