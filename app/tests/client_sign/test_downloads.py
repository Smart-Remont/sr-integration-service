"""Downloads. Error paths were compared with the stand; paths through MyNCA are covered only here."""

import io
import re
import zipfile
from types import SimpleNamespace

import httpx
import pytest
from asyncpg.exceptions import RaiseError
from httpx import AsyncClient

from src.config import ClientSignConfig
from src.features.client_sign import documents
from tests.client_sign.fakes import CONFIG, FakeConnection, Partners, install_overrides

ROUTES = ("download", "download-signed", "download-cms")
BASE = "/api/client_sign"
DS_FUNCTION = "ds.ds_get_by_uuid"
AGREEMENT_FUNCTION = "public.client_request_get_by_uuid"
STATEMENT_FUNCTION = "public.otbasy_statement_get_by_uuid"
CONVERT = "https://mynca.test/api/document/docx-to-pdf"
GROUP = "22222222-2222-2222-2222-222222222222"
GROUP_PDF = f"https://mynca.test/api/sign/group/{GROUP}/download"
GROUP_CMS = f"https://mynca.test/api/sign/group/{GROUP}/download-cms"
PARTNER_DS = "https://partner.test/partner/get_signed_file/5/DS/SIGNED/"
DS_DOCX = (
    "https://office.test/dict/ds-download/is_pdf/false/for_view/false/ds_id/5/client_request_id/7"
)
SIGNED_DS = {"ds_id": 5, "client_request_id": 7, "signed": True, "sign_group_id": GROUP}
UNSIGNED_DS = {"ds_id": 5, "client_request_id": 7, "ds_type_code": "OTHER", "signed": False}
HTML_TYPE = "text/html; charset=UTF-8"
JSON_TYPE = "application/json"


def assert_file(
    response: httpx.Response,
    content: bytes,
    content_type: str,
    file_name: str,
    *,
    has_length: bool = True,
) -> None:
    assert response.status_code == 200
    assert response.content == content
    raw = {name.decode().lower(): value for name, value in response.headers.raw}
    assert raw["content-type"] == content_type.encode()
    assert raw["content-disposition"] == f"attachment; filename={file_name}".encode()
    assert raw["content-description"] == b"File Transfer"
    assert raw["content-transfer-encoding"] == b"binary"
    assert raw["expires"] == b"0"
    assert raw["cache-control"] == b"must-revalidate, post-check=0, pre-check=0"
    assert raw["pragma"] == b"public"
    assert ("content-length" in raw) is has_length
    if has_length:
        assert raw["content-length"] == str(len(content)).encode()


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("params", ["", "/doc_type/XX/uuid/x", "/uuid/x", "?doc_type[]=DS&uuid=1"])
async def test_unknown_type_text(
    client: AsyncClient, connection: FakeConnection, route: str, params: str
) -> None:
    response = await client.get(f"{BASE}/{route}{params}")
    assert response.status_code == 200
    assert response.text == "Ошибка: Неизвестный тип документа"
    assert response.headers["content-type"] == HTML_TYPE
    assert response.headers["expires"] == "Thu, 19 Nov 1981 08:52:00 GMT"
    assert connection.calls == []


@pytest.mark.parametrize("route", ROUTES)
async def test_database_error_text_is_escaped(
    client: AsyncClient, connection: FakeConnection, route: str
) -> None:
    connection.handlers = {DS_FUNCTION: RaiseError("{Документ <не> найден}")}
    response = await client.get(f"{BASE}/{route}/doc_type/ds")
    assert response.text == "Ошибка: Документ &lt;не&gt; найден"


@pytest.mark.parametrize(("uuid", "bound"), [("", None), ("/uuid/0", None), ("/uuid/abc", "abc")])
async def test_zero_and_empty_uuid_are_null(
    client: AsyncClient,
    connection: FakeConnection,
    uuid: str,
    bound: str | None,
) -> None:
    connection.handlers = {DS_FUNCTION: []}
    await client.get(f"{BASE}/download-cms/doc_type/DS{uuid}")
    assert connection.called(DS_FUNCTION)[0][-1] == bound


@pytest.mark.parametrize(
    ("query", "location"),
    [
        ("/uuid/%2Fdocuments%2Fa%20b.pdf", "/documents/a b.pdf"),
        ("/uuid/documents%2Fa.pdf", "/documents/a.pdf"),
        ("", "/"),
        ("?uuid=http://example.com/a", "http://example.com/a"),
        ("?uuid=HTTP://example.com/a", "/HTTP://example.com/a"),
        ("?uuid[]=1", "/"),
        ("?uuid=//example.com", "/example.com"),
        ("?uuid=/a%0d%0ab", "/ab"),
        ("?uuid=%D0%B4.pdf", "/д.pdf"),
    ],
)
async def test_manual_agreement_redirects_as_zend_does(
    client: AsyncClient, connection: FakeConnection, query: str, location: str
) -> None:
    response = await client.get(f"{BASE}/download/doc_type/agreement_manual{query}")
    assert response.status_code == 302
    raw = {name.decode().lower(): value for name, value in response.headers.raw}
    assert raw["location"] == location.encode()
    assert raw["content-type"] == HTML_TYPE.encode()
    assert response.content == b""
    assert connection.calls == []


async def test_signed_document_comes_from_mynca(
    client: AsyncClient, connection: FakeConnection, partners: Partners
) -> None:
    connection.handlers = {DS_FUNCTION: [SIGNED_DS]}
    partners.routes[GROUP_PDF] = httpx.Response(
        200, content=b"%PDF signed", headers={"content-type": "application/pdf"}
    )
    for route in ("download", "download-signed"):
        response = await client.get(f"{BASE}/{route}/uuid/u/doc_type/DS")
        assert_file(response, b"%PDF signed", "application/pdf", "Доп_соглашение_5.pdf")
    assert partners.urls() == [GROUP_PDF, GROUP_PDF]
    assert partners.requests[0].headers["authorization"] == "Bearer test-mynca-token"


@pytest.mark.parametrize(
    ("doc_type", "function", "row", "name"),
    [
        (
            "AGREEMENT",
            AGREEMENT_FUNCTION,
            {"client_request_id": 7, "signed": True, "sign_group_id": GROUP},
            "Договор_оказания_услуг_7",
        ),
        (
            "OTBASY_STATEMENT",
            STATEMENT_FUNCTION,
            {"client_request_document_id": 3, "signed": True, "sign_group_id": GROUP},
            "Заявление_по_ОТБАСЫ_3",
        ),
    ],
)
async def test_file_names_by_type(
    client: AsyncClient,
    connection: FakeConnection,
    partners: Partners,
    doc_type: str,
    function: str,
    row: dict[str, object],
    name: str,
) -> None:
    connection.handlers = {function: [row]}
    partners.routes[GROUP_PDF] = httpx.Response(200, content=b"PDF")
    partners.routes[GROUP_CMS] = httpx.Response(200, content=b"CMS")
    pdf = await client.get(f"{BASE}/download-signed/uuid/u/doc_type/{doc_type}")
    assert_file(pdf, b"PDF", "application/pdf", f"{name}.pdf")
    cms = await client.get(f"{BASE}/download-cms/uuid/u/doc_type/{doc_type}")
    assert_file(cms, b"CMS", "application/pkcs7-signature", f"{name}.cms")


@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(500, content=b"x"),
        httpx.Response(200, content=b""),
        httpx.ConnectError("no"),
    ],
)
async def test_mynca_failure_falls_back_to_the_partner(
    client: AsyncClient,
    connection: FakeConnection,
    partners: Partners,
    answer: httpx.Response | Exception,
) -> None:
    connection.handlers = {DS_FUNCTION: [SIGNED_DS]}
    partners.routes[GROUP_PDF] = answer
    partners.routes[PARTNER_DS] = httpx.Response(200, content=b"%PDF xml")
    response = await client.get(f"{BASE}/download-signed/uuid/u/doc_type/DS")
    assert_file(
        response, b"%PDF xml", "application/pdf", "Подписанный документ.pdf", has_length=False
    )
    assert partners.requests[1].headers["content-type"] == "application/json"
    assert "authorization" not in partners.requests[1].headers


@pytest.mark.parametrize("route", ["download", "download-signed"])
async def test_no_group_goes_to_the_partner_and_any_answer_is_the_file(
    client: AsyncClient, connection: FakeConnection, partners: Partners, route: str
) -> None:
    connection.handlers = {DS_FUNCTION: [{**SIGNED_DS, "sign_group_id": None}]}
    partners.routes[PARTNER_DS] = httpx.Response(404, content=b"partner page")
    response = await client.get(f"{BASE}/{route}/uuid/u/doc_type/DS")
    assert_file(
        response, b"partner page", "application/pdf", "Подписанный документ.pdf", has_length=False
    )
    assert partners.urls() == [PARTNER_DS]


async def test_partner_failure_gives_an_empty_file(
    client: AsyncClient, connection: FakeConnection, partners: Partners
) -> None:
    connection.handlers = {DS_FUNCTION: [{**SIGNED_DS, "sign_group_id": None}]}
    partners.routes[PARTNER_DS] = httpx.ConnectError("no")
    response = await client.get(f"{BASE}/download-signed/uuid/u/doc_type/DS")
    assert_file(response, b"", "application/pdf", "Подписанный документ.pdf", has_length=False)


async def test_unknown_uuid_asks_the_partner_with_an_empty_id_as_php(
    client: AsyncClient, connection: FakeConnection, partners: Partners
) -> None:
    connection.handlers = {DS_FUNCTION: []}
    url = "https://partner.test/partner/get_signed_file//DS/SIGNED/"
    partners.routes[url] = httpx.Response(404, content=b"nf")
    response = await client.get(f"{BASE}/download-signed/uuid/u/doc_type/DS")
    assert_file(response, b"nf", "application/pdf", "Подписанный документ.pdf", has_length=False)


async def test_missing_partner_address_gives_503(
    client: AsyncClient, connection: FakeConnection, partners: Partners
) -> None:
    install_overrides(
        connection, partners, ClientSignConfig(**{**CONFIG.__dict__, "partner_url": ""})
    )
    connection.handlers = {DS_FUNCTION: [{**SIGNED_DS, "sign_group_id": None}]}
    response = await client.get(f"{BASE}/download-signed/uuid/u/doc_type/DS")
    assert response.status_code == 503
    assert partners.requests == []


@pytest.mark.parametrize("missing", ["mynca_base_url", "mynca_token"])
async def test_missing_mynca_gives_503(
    client: AsyncClient, connection: FakeConnection, partners: Partners, missing: str
) -> None:
    install_overrides(connection, partners, ClientSignConfig(**{**CONFIG.__dict__, missing: ""}))
    connection.handlers = {DS_FUNCTION: [SIGNED_DS]}
    response = await client.get(f"{BASE}/download-cms/uuid/u/doc_type/DS")
    assert response.status_code == 503
    assert partners.requests == []


async def test_cms_without_group_text(
    client: AsyncClient, connection: FakeConnection, partners: Partners
) -> None:
    connection.handlers = {DS_FUNCTION: [{**SIGNED_DS, "sign_group_id": "0"}]}
    response = await client.get(f"{BASE}/download-cms/uuid/u/doc_type/DS")
    assert response.text == "Ошибка: sign_group_id отсутствует"
    assert partners.requests == []


@pytest.mark.parametrize(
    ("answer", "text"),
    [
        (httpx.Response(200, content=b""), "Ошибка: Ошибка MyNCA: Не удалось получить CMS"),
        (
            httpx.Response(500, json={"error": "<e>"}),
            "Ошибка: Ошибка MyNCA: &lt;e&gt;",
        ),
        (
            httpx.Response(500, content=b"bad", headers={"content-type": "application/json"}),
            "Ошибка: Ошибка MyNCA: json_decode_error: Syntax error",
        ),
    ],
)
async def test_cms_failures(
    client: AsyncClient,
    connection: FakeConnection,
    partners: Partners,
    answer: httpx.Response,
    text: str,
) -> None:
    connection.handlers = {DS_FUNCTION: [SIGNED_DS]}
    partners.routes[GROUP_CMS] = answer
    response = await client.get(f"{BASE}/download-cms/uuid/u/doc_type/DS")
    assert response.status_code == 200
    assert response.text == text


async def test_unsigned_document_is_converted_by_mynca(
    client: AsyncClient, connection: FakeConnection, partners: Partners
) -> None:
    connection.handlers = {DS_FUNCTION: [UNSIGNED_DS]}
    partners.routes[DS_DOCX] = httpx.Response(200, content=b"DOCX")
    # base64_decode drops line breaks and takes a missing padding, as PHP does.
    partners.routes[CONVERT] = httpx.Response(200, json={"pdf": "JVBE\nRg"})
    response = await client.get(f"{BASE}/download/uuid/u/doc_type/DS")
    assert_file(response, b"%PDF", "application/pdf", "Доп_соглашение_5.pdf")


@pytest.mark.parametrize(
    ("answer", "text"),
    [
        (httpx.Response(500, json={"message": "нет"}), "Ошибка: нет"),
        (httpx.Response(500, content=b"x"), "Ошибка: Ошибка конвертации"),
        (httpx.Response(200, json={"a": 1}), "Ошибка: Ошибка конвертации"),
    ],
)
async def test_conversion_failure_text(
    client: AsyncClient,
    connection: FakeConnection,
    partners: Partners,
    answer: httpx.Response,
    text: str,
) -> None:
    connection.handlers = {DS_FUNCTION: [UNSIGNED_DS]}
    partners.routes[DS_DOCX] = httpx.Response(200, content=b"DOCX")
    partners.routes[CONVERT] = answer
    response = await client.get(f"{BASE}/download/uuid/u/doc_type/DS")
    assert response.text == text


async def test_no_document_for_download(
    client: AsyncClient, connection: FakeConnection, partners: Partners
) -> None:
    connection.handlers = {DS_FUNCTION: [UNSIGNED_DS]}
    partners.routes[DS_DOCX] = httpx.Response(200, content=b"")
    response = await client.get(f"{BASE}/download/uuid/u/doc_type/DS")
    assert response.text == "Ошибка: Не удалось получить документ"
    assert CONVERT not in partners.urls()


@pytest.mark.parametrize(
    ("row", "url"),
    [
        (
            {"ds_id": 48034, "client_request_id": 7, "ds_type_code": "ROOM_CHANGE"},
            "https://office.test/2601153_ds_tk_change.docx",
        ),
        (
            {"ds_id": 47606, "client_request_id": 7, "ds_type_code": "ROOM_CHANGE"},
            "https://office.test/2601153_ds_room_change.docx",
        ),
        (
            {"ds_id": 47606, "client_request_id": 7, "ds_type_code": "OTHER"},
            "https://office.test/dict/ds-download/is_pdf/false/for_view/false/ds_id/47606/client_request_id/7",
        ),
        (
            {"ds_id": 5, "client_request_id": 7, "ds_type_code": "AGREEMENT_BARTER"},
            "https://office.test/dict/report/mode/ds_agreement_barter/ds_id/5/doc/1",
        ),
        (
            {"ds_id": 5, "client_request_id": 7, "ds_type_code": "ROOM_CHANGE"},
            "https://myspace.test/client_request/7/ds/5/document/download/?is_sign=true",
        ),
    ],
)
async def test_where_a_ds_document_comes_from(
    client: AsyncClient,
    connection: FakeConnection,
    partners: Partners,
    row: dict[str, object],
    url: str,
) -> None:
    connection.handlers = {DS_FUNCTION: [row]}
    partners.routes[url] = httpx.Response(200, content=b"")
    await client.get(f"{BASE}/download/uuid/u/doc_type/DS")
    assert partners.urls() == [url]


async def test_statement_is_read_from_minio_first(
    client: AsyncClient,
    connection: FakeConnection,
    partners: Partners,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    keys: list[str] = []

    async def stored(key: str) -> bytes:
        keys.append(key)
        return b"DOCX"

    monkeypatch.setattr(documents, "minio_config", SimpleNamespace(is_configured=True))
    monkeypatch.setattr(documents, "get_object", stored)
    connection.handlers = {
        STATEMENT_FUNCTION: [
            {
                "client_request_document_id": 3,
                "document_url": "/documents/2026.01.01/a.docx",
                "signed": False,
            }
        ]
    }
    partners.routes[CONVERT] = httpx.Response(200, json={"pdf": "UERG"})
    response = await client.get(f"{BASE}/download/uuid/u/doc_type/OTBASY_STATEMENT")
    assert keys == ["documents/2026.01.01/a.docx"]
    assert_file(response, b"PDF", "application/pdf", "Заявление_по_ОТБАСЫ_3.pdf")
    assert partners.urls() == [CONVERT]


async def test_statement_falls_back_to_the_office_address(
    client: AsyncClient,
    connection: FakeConnection,
    partners: Partners,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def broken(key: str) -> bytes:
        raise RuntimeError(key)

    monkeypatch.setattr(documents, "minio_config", SimpleNamespace(is_configured=True))
    monkeypatch.setattr(documents, "get_object", broken)
    connection.handlers = {
        STATEMENT_FUNCTION: [
            {"client_request_document_id": 3, "document_url": "/documents/a.docx", "signed": False}
        ]
    }
    partners.routes["https://office.test/documents/a.docx"] = httpx.Response(200, content=b"")
    response = await client.get(f"{BASE}/download/uuid/u/doc_type/OTBASY_STATEMENT")
    assert response.text == "Ошибка: Не удалось получить документ"


@pytest.mark.parametrize(
    ("route", "function"),
    [("download-ds", DS_FUNCTION), ("download-agreement", AGREEMENT_FUNCTION)],
)
async def test_integration_unknown_document_is_400_with_empty_body(
    client: AsyncClient, connection: FakeConnection, route: str, function: str
) -> None:
    connection.handlers = {function: []}
    response = await client.get(f"{BASE}/{route}/uuid/x")
    assert response.status_code == 400
    assert response.content == b""
    assert response.headers["content-type"] == JSON_TYPE
    assert response.headers["pragma"] == "no-cache"


async def test_integration_database_error_text_goes_before_the_400(
    client: AsyncClient, connection: FakeConnection
) -> None:
    connection.handlers = {DS_FUNCTION: RaiseError("{Документ <не> найден}")}
    response = await client.get(f"{BASE}/download-ds")
    assert response.status_code == 400
    assert response.text == "Документ <не> найден"
    assert connection.called(DS_FUNCTION)[0][-1] is None


async def test_integration_signed_document_comes_from_the_partner(
    client: AsyncClient, connection: FakeConnection, partners: Partners
) -> None:
    connection.handlers = {DS_FUNCTION: [SIGNED_DS]}
    partners.routes[PARTNER_DS] = httpx.Response(200, content=b"%PDF")
    response = await client.get(f"{BASE}/download-ds/uuid/u")
    assert_file(response, b"%PDF", "application/pdf", "Подписанный документ.pdf", has_length=False)
    assert partners.urls() == [PARTNER_DS]


INTEGRATION_DS_DOCX = "https://office.test/dict/ds-download/for_view/1/ds_id/5/client_request_id/7"
INTEGRATION_AGREEMENT_DOCX = (
    "https://office.test/dict/report/mode/contract/is_pdf/false/for_view/true/client_request_id/7"
)
UNSIGNED_AGREEMENT = {"client_request_id": 7, "signed": False}


def real_docx() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", "<w/>")
        archive.writestr("[Content_Types].xml", "<t/>")
    return buffer.getvalue()


@pytest.mark.parametrize(
    ("route", "function", "row", "docx_url"),
    [
        ("download-ds", DS_FUNCTION, UNSIGNED_DS, INTEGRATION_DS_DOCX),
        ("download-agreement", AGREEMENT_FUNCTION, UNSIGNED_AGREEMENT, INTEGRATION_AGREEMENT_DOCX),
    ],
)
async def test_integration_unsigned_document_is_converted_by_mynca(
    client: AsyncClient,
    connection: FakeConnection,
    partners: Partners,
    route: str,
    function: str,
    row: dict[str, object],
    docx_url: str,
) -> None:
    connection.handlers = {function: [row]}
    partners.routes[docx_url] = httpx.Response(200, content=real_docx())
    partners.routes[CONVERT] = httpx.Response(200, json={"pdf": "JVBERg=="})

    response = await client.get(f"{BASE}/{route}/uuid/u")

    assert response.status_code == 200
    assert response.content == b"%PDF"
    assert response.headers["content-type"] == "application/pdf"
    assert re.fullmatch(
        r"attachment; filename=auto_[0-9a-f]{32}\.pdf",
        response.headers["content-disposition"],
    )
    assert response.headers["pragma"] == "public"


async def test_integration_is_base64_answers_with_text(
    client: AsyncClient, connection: FakeConnection, partners: Partners
) -> None:
    connection.handlers = {DS_FUNCTION: [UNSIGNED_DS]}
    partners.routes[INTEGRATION_DS_DOCX] = httpx.Response(200, content=real_docx())
    partners.routes[CONVERT] = httpx.Response(200, json={"pdf": "JVBERg=="})

    response = await client.get(f"{BASE}/download-ds/is_base64/true/uuid/u")

    assert response.status_code == 200
    assert response.text == "JVBERg=="
    assert response.headers["content-type"].startswith("text/html")


@pytest.mark.parametrize(
    ("for_view", "row", "url"),
    [
        (
            "false",
            UNSIGNED_DS,
            "https://office.test/dict/ds-download/for_view/false/ds_id/5/client_request_id/7",
        ),
        (
            "false",
            {**UNSIGNED_DS, "ds_type_code": "ROOM_CHANGE"},
            "https://myspace.test/client_request/7/ds/5/document/download/",
        ),
        (
            "0",
            {**UNSIGNED_DS, "ds_type_code": "ROOM_CHANGE"},
            "https://myspace.test/client_request/7/ds/5/document/download/?is_sign=true",
        ),
        (
            "1",
            {**UNSIGNED_DS, "ds_type_code": "AGREEMENT_BARTER"},
            "https://office.test/dict/report/mode/ds_agreement_barter/ds_id/5/doc/1",
        ),
    ],
)
async def test_integration_ds_address_follows_php(
    client: AsyncClient,
    connection: FakeConnection,
    partners: Partners,
    for_view: str,
    row: dict[str, object],
    url: str,
) -> None:
    connection.handlers = {DS_FUNCTION: [row]}
    partners.routes[url] = httpx.Response(200, content=real_docx())
    partners.routes[CONVERT] = httpx.Response(200, json={"pdf": "JVBERg=="})

    response = await client.get(f"{BASE}/download-ds/uuid/u/for_view/{for_view}")

    assert response.status_code == 200
    assert partners.urls()[0] == url


@pytest.mark.parametrize("content", [b"", b"<html>not a document</html>"])
async def test_integration_source_that_is_not_docx_is_400_without_conversion(
    client: AsyncClient, connection: FakeConnection, partners: Partners, content: bytes
) -> None:
    connection.handlers = {DS_FUNCTION: [UNSIGNED_DS]}
    partners.routes[INTEGRATION_DS_DOCX] = httpx.Response(200, content=content)

    response = await client.get(f"{BASE}/download-ds/uuid/u")

    assert response.status_code == 400
    assert response.content == b""
    assert CONVERT not in partners.urls()


async def test_integration_failed_conversion_is_400(
    client: AsyncClient, connection: FakeConnection, partners: Partners
) -> None:
    connection.handlers = {DS_FUNCTION: [UNSIGNED_DS]}
    partners.routes[INTEGRATION_DS_DOCX] = httpx.Response(200, content=real_docx())
    partners.routes[CONVERT] = httpx.Response(500, json={"message": "нет"})

    response = await client.get(f"{BASE}/download-ds/uuid/u")

    assert response.status_code == 400
    assert response.content == b""
