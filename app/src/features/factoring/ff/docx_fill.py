"""Fill Freedom factoring DOCX templates.

Bank templates use ``{{placeholder}}``; Word often splits them across ``w:t`` runs.
Matches are found on the paragraph's joined text; each replacement is written into
the run where the placeholder starts, so formatting of other runs is preserved.

Table rows containing ``{{ item.<key> }}`` (or the legacy ``{{n}}`` markers) are
repeated once per entry of ``row_values``; ``item.<key>`` resolves to ``<key>``.
"""

from __future__ import annotations

import io
import re
import zipfile
from xml.sax.saxutils import escape, unescape

_PLACEHOLDER_RE = re.compile(r"\{\{\s*(.*?)\s*\}\}", re.DOTALL)
_WT_RE = re.compile(r"(<w:t(?:\s[^>]*)?>)(.*?)(</w:t>)", re.DOTALL)
_WP_RE = re.compile(r"<w:p\b[\s\S]*?</w:p>")
_TR_RE = re.compile(r"<w:tr\b[\s\S]*?</w:tr>")
_STATIC_CONTRACT = "{номер договора ЮД статично}"
_ROW_MARKERS = ("{{n}}", "{{uuid}}", "{{credit_contract}}")
_ITEM_ROW_RE = re.compile(r"\{\{\s*item\.")

_ALIASES = {
    "borrower full_name": "borrower_full_name",
    "borrower.otp": "borrower_otp",
    "total amount": "total_amount",
}


def fill_docx_bytes(
    docx_bytes: bytes,
    values: dict[str, str],
    row_values: list[dict[str, str]] | None = None,
) -> bytes:
    source = zipfile.ZipFile(io.BytesIO(docx_bytes))
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as dest:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename.startswith("word/") and item.filename.endswith(".xml"):
                text = data.decode("utf-8")
                if row_values is not None:
                    text = _expand_table_rows(text, values, row_values)
                data = _fill_xml_text(text, values).encode("utf-8")
            dest.writestr(item, data)
    return output.getvalue()


def _placeholder_key(raw: str) -> str:
    raw = re.sub(r"\s+", " ", raw).strip()
    if raw.startswith("item."):
        raw = raw[len("item.") :]
    return _ALIASES.get(raw, raw.replace(" ", "_").replace(".", "_"))


def _replacements(text: str, values: dict[str, str]) -> list[tuple[int, int, str]]:
    spans: list[tuple[int, int, str]] = []
    for match in _PLACEHOLDER_RE.finditer(text):
        key = _placeholder_key(match.group(1))
        if key in values:
            spans.append((match.start(), match.end(), values[key]))
    contract = values.get("contract_number")
    if contract:
        start = text.find(_STATIC_CONTRACT)
        while start != -1:
            spans.append((start, start + len(_STATIC_CONTRACT), contract))
            start = text.find(_STATIC_CONTRACT, start + len(_STATIC_CONTRACT))
    return sorted(spans)


def replace_placeholders(text: str, values: dict[str, str]) -> str:
    result: list[str] = []
    cursor = 0
    for start, end, value in _replacements(text, values):
        result.append(text[cursor:start])
        result.append(value)
        cursor = end
    result.append(text[cursor:])
    return "".join(result)


def _expand_table_rows(
    xml_text: str,
    values: dict[str, str],
    row_values: list[dict[str, str]],
) -> str:
    def _repl_tr(match: re.Match[str]) -> str:
        row = match.group(0)
        joined = "".join(_xml_text(inner) for _, inner, _ in _WT_RE.findall(row))
        compact = re.sub(r"\s+", "", joined)
        if not (_ITEM_ROW_RE.search(joined) or any(marker in compact for marker in _ROW_MARKERS)):
            return row
        return "".join(_fill_xml_text(row, {**values, **item}) for item in row_values)

    return _TR_RE.sub(_repl_tr, xml_text)


def _fill_xml_text(text: str, values: dict[str, str]) -> str:
    def _fill_paragraph(match: re.Match[str]) -> str:
        paragraph = match.group(0)
        parts = _WT_RE.findall(paragraph)
        if not parts:
            return paragraph
        texts = [_xml_text(inner) for _, inner, _ in parts]
        joined = "".join(texts)
        spans = _replacements(joined, values)
        if not spans:
            return paragraph

        new_texts: list[str] = []
        offset = 0
        for segment in texts:
            seg_start, seg_end = offset, offset + len(segment)
            offset = seg_end
            out: list[str] = []
            pos = seg_start
            for start, end, value in spans:
                if end <= seg_start or start >= seg_end:
                    continue
                if start >= pos:
                    out.append(joined[pos:start])
                    out.append(value)
                    pos = min(end, seg_end)
                elif end > pos:
                    pos = min(end, seg_end)
            out.append(joined[pos:seg_end])
            new_texts.append("".join(out))

        index = 0

        def _put_text(wt_match: re.Match[str]) -> str:
            nonlocal index
            body = new_texts[index]
            index += 1
            open_tag = wt_match.group(1)
            if "xml:space" not in open_tag:
                open_tag = open_tag[:-1] + ' xml:space="preserve">'
            return f"{open_tag}{escape(body)}{wt_match.group(3)}"

        return _WT_RE.sub(_put_text, paragraph)

    return _WP_RE.sub(_fill_paragraph, text)


def _xml_text(fragment: str) -> str:
    return unescape(re.sub(r"<[^>]+>", "", fragment))
