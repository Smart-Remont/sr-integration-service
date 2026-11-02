"""The two list pages. The not-found page is the real answer of the stand, warnings removed."""

from pathlib import Path

from asyncpg.exceptions import RaiseError
from httpx import AsyncClient

from src.features.client_sign.views import render_sign_doc_list
from tests.client_sign.fakes import FakeConnection

ROUTE = "/api/client_sign/sign-doc-list"
OLD_ROUTE = "/api/client_sign/old-sign-doc-list"
GOLDEN = Path(__file__).parent / "golden" / "sign_doc_list_not_found.html"
HASH_LOOKUP = "client.sign_client_request_id_get"
CLIENT_INFO = "public.get_client_info"
DOCUMENTS = "client.sign_request_doc_read"
DELETED = "client.sign_deleted_read"
HTML_TYPE = "text/html; charset=UTF-8"
INFO = {
    "resident_name": 'ЖК "Тест"',
    "client_name": "Иванов <Иван>",
    "orig_image_url": "/documents/plan.png?a=1&b=2",
    "planirovka_name": "1Б",
    "city_name": "Алматы",
}
UNSIGNED_DS = {
    "document_type_name": "ДС <1>",
    "sign_id": 0,
    "contract_signer_sign_id": 0,
    "doc_type": "DS",
    "signed": False,
    "uuid": "ds-uuid",
    "show_sign_buttons": True,
}
SIGNED_DS = {
    "document_type_name": "ДС",
    "sign_id": 9,
    "dn_name": "CN=ИВАНОВ ИВАН,SN=ИВАНОВ,GN=ИВАНОВИЧ,IIN123456789012",
    "contract_signer_sign_id": 5,
    "contract_signer_dn_name": "CN=ПЕТРОВ ПЁТР,SN=ПЕТРОВ,BIN987654321098,IIN111111111111",
    "doc_type": "DS",
    "signed": True,
    "uuid": "ds-uuid",
    "sign_date_time": "01.02.2026 10:00",
    "contract_signer_date_time": "02.02.2026 11:00",
    "show_sign_buttons": False,
}
RAW_STYLE = (
    "margin-top:6px; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "
    '\\"Liberation Mono\\", \\"Courier New\\", monospace; font-size:12px; background:#fff; '
    "border:1px solid var(--clr-border); border-radius:8px; padding:10px; white-space:pre-wrap; "
    "word-break:break-word;"
)


def render(*documents: dict, deleted: tuple[dict, ...] = (), info: dict | None = None) -> str:
    return render_sign_doc_list(
        request_hash="the-hash",
        client_info=INFO if info is None else info,
        documents=list(documents),
        deleted_signatures=list(deleted),
    ).decode()


def details(raw: str, indent: str = "                                ") -> str:
    return (
        f'{indent}<details style="margin-top:8px;">\n'
        f'{indent}    <summary style="cursor:pointer; color:var(--clr-muted); font-size:12px;">'
        "Реквизиты сертификата</summary>\n"
        f'{indent}    <div style="{RAW_STYLE}">{raw}</div>\n'
        f"{indent}</details>\n"
    )


def row(icon: str, color: str, label: str, value: str, indent: str) -> str:
    return (
        f'{indent}    <div class="cs-sign-info-row">\n'
        f'{indent}        <i class="fa {icon}" style="color:{color}"></i>\n'
        f"{indent}        <div>\n"
        f'{indent}            <span class="cs-sign-info-label">{label}</span>\n'
        f'{indent}            <span class="cs-sign-info-value">{value}</span>\n'
        f"{indent}        </div>\n"
        f"{indent}    </div>\n"
    )


async def test_unknown_hash_gives_the_page_php_gives(
    client: AsyncClient, connection: FakeConnection
) -> None:
    connection.handlers = {HASH_LOOKUP: None}
    response = await client.get(f"{ROUTE}/remont/nonexistent")
    assert response.status_code == 200
    assert response.content == GOLDEN.read_bytes()
    assert response.headers["content-type"] == HTML_TYPE
    assert response.headers["cache-control"] == "no-store, no-cache, must-revalidate"
    assert response.headers["expires"] == "Thu, 19 Nov 1981 08:52:00 GMT"
    assert response.headers["pragma"] == "no-cache"
    assert "set-cookie" not in response.headers
    # PHP goes on with no request: the functions get NULL.
    assert connection.called(DOCUMENTS)[0][0:] == (None,)


async def test_hash_comes_from_address_query_and_form(
    client: AsyncClient, connection: FakeConnection
) -> None:
    connection.handlers = {HASH_LOOKUP: None}
    await client.get(f"{ROUTE}/remont/from-address/")
    await client.get(ROUTE, params={"remont": "from-query"})
    await client.post(ROUTE, data={"remont": "from-form"})
    await client.get(f"{ROUTE}?remont[]=1")
    assert [args[0] for args in connection.called(HASH_LOOKUP)] == [
        "from-address",
        "from-query",
        "from-form",
        "Array",
    ]


async def test_failed_hash_lookup_gives_the_error_page(
    client: AsyncClient, connection: FakeConnection
) -> None:
    connection.handlers = {HASH_LOOKUP: RaiseError("boom")}
    response = await client.get(f"{ROUTE}/remont/x")
    assert response.status_code == 200
    body = response.text
    assert body.startswith("<!doctype html>")
    assert (
        '</head>\n<body>\n\n    <div class="cs-page-header">\n        <div class="cs-container">\n'
        "            <h1>Подписание документов</h1>\n"
    ) in body
    assert body.endswith(
        '            <i class="fa fa-exclamation-circle"></i>\n            <h3>Ошибка</h3>\n'
        "            <p>Заявка не найдена</p>\n        </div>\n    </div>\n</body>\n</html>\n"
    )
    assert connection.called(DOCUMENTS) == []


async def test_invalid_encoding_reaches_the_error_page_without_a_database_call(
    client: AsyncClient, connection: FakeConnection
) -> None:
    response = await client.get(f"{ROUTE}?remont=%ff")
    assert "<p>Заявка не найдена</p>" in response.text
    assert connection.called(HASH_LOOKUP) == []


async def test_failed_reads_leave_their_part_empty(
    client: AsyncClient, connection: FakeConnection
) -> None:
    connection.handlers = {
        HASH_LOOKUP: 7,
        CLIENT_INFO: RaiseError("a"),
        DOCUMENTS: RaiseError("b"),
        DELETED: RaiseError("c"),
    }
    response = await client.get(f"{ROUTE}/remont/x")
    assert response.status_code == 200
    assert "Нет документов для подписания" in response.text
    assert "Нет отозванных подписей" in response.text
    assert connection.called(DELETED) == [("7",)]


async def test_page_with_rows_from_the_database(
    client: AsyncClient, connection: FakeConnection
) -> None:
    connection.handlers = {
        HASH_LOOKUP: 7,
        CLIENT_INFO: [INFO],
        DOCUMENTS: [UNSIGNED_DS],
        DELETED: [],
    }
    response = await client.get(f"{ROUTE}/remont/the-hash")
    assert (
        'href="/client-sign/perform-sign/uuid/ds-uuid/doc_type/DS/hash/the-hash"' in response.text
    )


def test_header_escapes_as_php_and_leaves_the_image_address_raw() -> None:
    page = render()
    assert '<div class="subtitle">ЖК &quot;Тест&quot; &mdash; Иванов &lt;Иван&gt;</div>' in page
    assert '<a href="/documents/plan.png?a=1&b=2" target="_blank">' in page


def test_no_client_info_prints_empty_values() -> None:
    page = render(info={})
    assert '<div class="subtitle"> &mdash; </div>' in page
    assert '<a href="" target="_blank">' in page


def test_empty_lists_keep_php_whitespace() -> None:
    page = render()
    assert (
        '<div class="cs-tab-panel active" id="cs-tab-active">\n'
        '                    <div class="cs-empty-state">\n'
        '                <i class="fa fa-folder-open-o"></i>\n'
        "                <p>Нет документов для подписания</p>\n"
        "            </div>\n"
        "            </div>\n"
    ) in page
    assert '<span class="cs-badge-cnt">0</span>' in page


def test_unsigned_card_has_download_and_sign_buttons() -> None:
    page = render(UNSIGNED_DS)
    assert (
        '                        <div class="cs-doc-title">ДС &lt;1&gt;</div>\n'
        "                                                    "
        '<span class="cs-doc-badge cs-doc-badge-unsigned">'
        '<i class="fa fa-clock-o"></i> Не подписан</span>\n'
        "                                            </div>\n"
    ) in page
    assert (
        '                    <div class="cs-doc-actions">\n'
        "                                                        "
        '<a href="/client-sign/download/uuid/ds-uuid/doc_type/DS" '
        'class="cs-btn-action cs-btn-download">\n'
        '                                    <i class="fa fa-download"></i> Скачать\n'
        "                                </a>\n"
        "                            "
        "                            "
        '<a href="/client-sign/perform-sign/uuid/ds-uuid/doc_type/DS/hash/the-hash"\n'
        '                               class="cs-btn-action cs-btn-sign"\n'
        '                               onclick="return confirmSign(this);">\n'
    ) in page
    assert '<span class="cs-badge-cnt">1</span>' in page


def test_signed_card_with_client_and_manager_signature() -> None:
    page = render(SIGNED_DS)
    client_block = (
        '                        <div class="cs-sign-info cs-sign-info-client">\n'
        + row(
            "fa-check-circle", "var(--clr-success)", "Подписал:", "ИВАНОВ ИВАН ИВАНОВИЧ", " " * 24
        )
        + "                            "
        + row("fa-id-card-o", "var(--clr-muted)", "ИИН:", "123456789012", " " * 28)
        + "                            "
        + "                            "
        + row("fa-calendar", "var(--clr-muted)", "Дата:", "01.02.2026 10:00", " " * 28)
        + "                            "
        + "                            "
        + details("CN=ИВАНОВ ИВАН,SN=ИВАНОВ,GN=ИВАНОВИЧ,IIN123456789012")
        + "                            \n"
        + "                            "
        + "                        </div>\n"
    )
    assert client_block in page
    manager_block = (
        '                        <div class="cs-sign-info cs-sign-info-manager">\n'
        + row("fa-user-circle", "#0369a1", "Подписал менеджер:", "ПЕТРОВ ПЁТР", " " * 24)
    )
    assert manager_block in page
    assert ">БИН:</span>" in page
    assert ">987654321098</span>" in page
    assert ">02.02.2026 11:00</span>" in page
    assert 'href="/client-sign/download-signed/uuid/ds-uuid/doc_type/DS"' in page
    assert 'href="/client-sign/download-cms/uuid/ds-uuid/doc_type/DS"' in page
    assert "Подписать\n" not in page


def test_signature_date_keeps_br_and_empty_name_shows_a_dash() -> None:
    page = render({**SIGNED_DS, "dn_name": "", "sign_date_time": "<b>1</b><br/>2"})
    assert '<span class="cs-sign-info-value">—</span>' in page
    assert '<span class="cs-sign-info-value">1&lt;br/&gt;2</span>' in page


def test_manual_agreement_links_to_the_file_without_signature_block() -> None:
    page = render(
        {
            "document_type_name": 'Договор "1"',
            "sign_id": 0,
            "contract_signer_sign_id": 0,
            "doc_type": "AGREEMENT_MANUAL",
            "signed": False,
            "uuid": "/documents/a.pdf",
            "show_sign_buttons": True,
        }
    )
    assert '<i class="fa fa-pencil"></i> Вручную</span>' in page
    assert ">Документ подписан вручную</span>" in page
    assert (
        '<a href="/documents/a.pdf" class="cs-btn-action cs-btn-download" '
        'download="Договор &quot;1&quot;">'
    ) in page
    assert "perform-sign" not in page


def test_statement_has_a_sign_button_and_no_download() -> None:
    page = render(
        {
            "document_type_name": "Заявление",
            "sign_id": 0,
            "contract_signer_sign_id": 0,
            "doc_type": "OTBASY_STATEMENT",
            "signed": False,
            "uuid": "u",
            "show_sign_buttons": True,
        }
    )
    assert "/client-sign/download/" not in page
    assert 'href="/client-sign/perform-sign/uuid/u/doc_type/OTBASY_STATEMENT/hash/the-hash"' in page


def test_revoked_signatures_group_by_code_and_take_the_first_name() -> None:
    page = render(
        deleted=(
            {
                "group_code": "A",
                "document_type_name": None,
                "sign_id": 0,
                "contract_signer_sign_id": 4,
                "contract_signer_dn_name": "CN=ПЕТРОВ ПЁТР,SN=ПЕТРОВ,IIN111111111111",
                "contract_signer_date_time": "03.03.2026 09:00",
            },
            {
                "group_code": "A",
                "document_type_name": "Договор",
                "sign_id": 3,
                "dn_name": "plain name",
                "sign_date_time": "02.03.2026 09:00",
                "contract_signer_sign_id": 0,
            },
            {
                "group_code": "B",
                "document_type_name": "ДС",
                "sign_id": 8,
                "dn_name": "",
                "sign_date_time": None,
                "contract_signer_sign_id": 0,
            },
        )
    )
    assert '<span class="cs-badge-cnt">2</span>' in page
    assert page.count('cs-doc-badge-revoked">') == 2
    assert '<div class="cs-doc-title">Договор</div>' in page
    # A client signature goes before a manager signature of the same group.
    assert page.index(">plain name</span>") < page.index(">Подписал менеджер:</span>")
    assert ">111111111111</span>" in page
    assert "Нет отозванных подписей" not in page


async def test_old_address_is_one_fixed_page_without_the_database(
    client: AsyncClient, connection: FakeConnection
) -> None:
    first = await client.get(OLD_ROUTE)
    second = await client.post(f"{OLD_ROUTE}/remont/abc?x=1", data={"a": "b"})
    assert first.status_code == 200
    assert first.content == second.content
    assert first.headers["content-type"] == HTML_TYPE
    assert first.content.startswith(b'<!doctype html>\n<html lang="ru" prefix="og: //ogp.me/ns#">')
    assert b"<b>Warning</b>" not in first.content
    assert connection.calls == []
