"""``client-sign/perform-sign``. Never run on the stand: it would create a real signing process."""

import base64
import json
from dataclasses import replace

import httpx
import pytest
from asyncpg.exceptions import RaiseError
from httpx import AsyncClient

from src.config import ClientSignConfig, app_config
from src.features.client_sign.callback_key import callback_key
from tests.client_sign.fakes import CONFIG, FakeConnection, Partners, install_overrides

ROUTE = "/api/client_sign/perform-sign"
DS_FUNCTION = "ds.ds_get_by_uuid"
AGREEMENT_FUNCTION = "public.client_request_get_by_uuid"
STATEMENT_FUNCTION = "public.otbasy_statement_get_by_uuid"
CONVERT = "https://mynca.test/api/document/docx-to-pdf"
CREATE = "https://mynca.test/api/sign/create"
DS_ROW = {
    "ds_id": 5,
    "client_request_id": 7,
    "ds_type_code": "CHANGE_MATERIAL",
    "signed": False,
}
DS_DOCX = (
    "https://office.test/dict/ds-download/is_pdf/false/for_view/false/ds_id/5/client_request_id/7"
)
SIGN_URL = "https://nca.test/sign/abc"


def page(message: str) -> bytes:
    return (
        '<div style="padding:40px;font-family:sans-serif;">'
        '<h3 style="color:#c00;">Ошибка подписания</h3>'
        f"<p>{message}</p>"
        '<p><a href="javascript:history.back();">Вернуться назад</a></p></div>'
    ).encode()


def created() -> httpx.Response:
    return httpx.Response(
        200,
        json={"data": {"sign_url": SIGN_URL, "sign_process_id": "p", "group_id": "g"}},
    )


def route_happy_path(connection: FakeConnection, partners: Partners) -> None:
    connection.handlers = {DS_FUNCTION: [DS_ROW]}
    partners.routes[DS_DOCX] = httpx.Response(200, content=b"DOCX")
    partners.routes[CONVERT] = httpx.Response(200, json={"pdf": "UERG"})
    partners.routes[CREATE] = created()


async def test_redirects_to_mynca_with_the_payload_php_sends(
    client: AsyncClient, connection: FakeConnection, partners: Partners
) -> None:
    route_happy_path(connection, partners)
    response = await client.get(f"{ROUTE}/uuid/u-1/doc_type/ds/hash/h-1")
    assert response.status_code == 302
    assert response.headers["location"] == SIGN_URL
    assert response.content == b""
    assert partners.urls() == [DS_DOCX, CONVERT, CREATE]
    convert = partners.requests[1]
    assert json.loads(convert.content) == {
        "docx": base64.b64encode(b"DOCX").decode(),
        "is_file": False,
    }
    assert convert.headers["authorization"] == "Bearer test-mynca-token"
    key = callback_key(CONFIG.callback_secret, "CLIENT_DS_SIGN", 5)
    assert json.loads(partners.requests[2].content) == {
        "file_name": "Доп_соглашение_5.pdf",
        "file_content": "UERG",
        "ext_id": 5,
        "meta_data": {
            "type_code": "CLIENT_DS_SIGN",
            "doc_type": "DS",
            "hash": "h-1",
            "uuid": "u-1",
            "client_request_id": 7,
            "ds_id": 5,
            "client_request_document_id": None,
        },
        "back_url": f"https://office.test/client-sign/callback/key/{key}",
        "return_url": "https://office.test/client-sign/sign-doc-list/remont/h-1",
        "exp_minutes": 60,
    }
    assert "\\u" not in partners.requests[2].content.decode()
    assert connection.called(DS_FUNCTION)[0][-1] == "u-1"


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ("/doc_type/DS", "UUID документа не указан"),
        ("/uuid/0/doc_type/XX", "Неизвестный тип документа: XX"),
        ("/uuid/u", "Неизвестный тип документа: "),
        ("/uuid/u/doc_type/agreement_manual", "Неизвестный тип документа: AGREEMENT_MANUAL"),
        ("/uuid/u/doc_type/%3Cb%3E", "Неизвестный тип документа: &lt;B&gt;"),
    ],
)
async def test_checks_before_anything_else(
    client: AsyncClient,
    connection: FakeConnection,
    partners: Partners,
    params: str,
    message: str,
) -> None:
    response = await client.get(f"{ROUTE}{params}")
    assert response.status_code == 200
    assert response.content == page(message)
    assert response.headers["content-type"] == "text/html; charset=UTF-8"
    assert connection.calls == []
    assert partners.requests == []


async def test_array_doc_type_prints_empty_as_php_strtoupper_null(client: AsyncClient) -> None:
    response = await client.get(f"{ROUTE}/uuid/u?doc_type[]=DS")
    assert response.content == page("Неизвестный тип документа: ")


@pytest.mark.parametrize(
    "missing",
    ["mynca_base_url", "mynca_token", "public_url", "callback_secret"],
)
async def test_missing_configuration_gives_503_before_the_database(
    client: AsyncClient,
    connection: FakeConnection,
    partners: Partners,
    missing: str,
) -> None:
    values = {**CONFIG.__dict__, missing: ""}
    install_overrides(connection, partners, ClientSignConfig(**values))
    response = await client.get(f"{ROUTE}/uuid/u/doc_type/DS")
    assert response.status_code == 503
    assert connection.calls == []
    assert partners.requests == []


async def test_missing_document_address_gives_503(
    client: AsyncClient, connection: FakeConnection, partners: Partners
) -> None:
    values = {**CONFIG.__dict__, "office_url": ""}
    install_overrides(connection, partners, ClientSignConfig(**values))
    connection.handlers = {DS_FUNCTION: [DS_ROW]}
    response = await client.get(f"{ROUTE}/uuid/u/doc_type/DS")
    assert response.status_code == 503
    assert partners.requests == []


async def test_database_error_text(client: AsyncClient, connection: FakeConnection) -> None:
    connection.handlers = {DS_FUNCTION: RaiseError("{Документ не найден}")}
    response = await client.get(f"{ROUTE}/uuid/u/doc_type/DS")
    assert response.content == page("Ошибка получения данных документа: Документ не найден")


@pytest.mark.parametrize(
    "document",
    [httpx.Response(200, content=b""), httpx.Response(500, content=b"x"), httpx.ConnectError("no")],
)
async def test_no_document(
    client: AsyncClient,
    connection: FakeConnection,
    partners: Partners,
    document: httpx.Response | Exception,
) -> None:
    connection.handlers = {DS_FUNCTION: [DS_ROW]}
    partners.routes[DS_DOCX] = document
    response = await client.get(f"{ROUTE}/uuid/u/doc_type/DS")
    assert response.content == page("Не удалось получить документ для подписания")
    assert partners.urls() == [DS_DOCX]


@pytest.mark.parametrize(
    ("answer", "message"),
    [
        (
            httpx.Response(500, json={"message": "плохой файл"}),
            "Ошибка конвертации docx→pdf: плохой файл",
        ),
        (httpx.Response(500, json={"error": "e1"}), "Ошибка конвертации docx→pdf: e1"),
        (httpx.Response(500, content=b"x"), "Ошибка конвертации docx→pdf: Неизвестная ошибка"),
        (httpx.Response(200, json={"a": 1}), "MyNCA API вернул пустой PDF"),
        (httpx.Response(200, json={"pdf": ""}), "MyNCA API вернул пустой PDF"),
    ],
)
async def test_conversion_failures(
    client: AsyncClient,
    connection: FakeConnection,
    partners: Partners,
    answer: httpx.Response,
    message: str,
) -> None:
    route_happy_path(connection, partners)
    partners.routes[CONVERT] = answer
    response = await client.get(f"{ROUTE}/uuid/u/doc_type/DS")
    assert response.content == page(message)
    assert CREATE not in partners.urls()


async def test_binary_pdf_answer_is_encoded_for_sign_create(
    client: AsyncClient, connection: FakeConnection, partners: Partners
) -> None:
    route_happy_path(connection, partners)
    partners.routes[CONVERT] = httpx.Response(
        200, content=b"%PDF", headers={"content-type": "application/pdf"}
    )
    await client.get(f"{ROUTE}/uuid/u/doc_type/DS")
    assert json.loads(partners.requests[2].content)["file_content"] == "JVBERg=="


@pytest.mark.parametrize(
    ("answer", "message"),
    [
        (httpx.Response(400, json={"message": "нет"}), "нет"),
        (httpx.Response(400, json={"error": "e2"}), "e2"),
        (httpx.Response(502, json={}), "Неизвестная ошибка MyNCA API (code=502)"),
        (httpx.Response(200, json={"data": {}}), "Неизвестная ошибка MyNCA API (code=200)"),
        (
            httpx.Response(502, content=b"<html>"),
            "json_decode_error: Syntax error",
        ),
    ],
)
async def test_sign_create_failures(
    client: AsyncClient,
    connection: FakeConnection,
    partners: Partners,
    answer: httpx.Response,
    message: str,
) -> None:
    route_happy_path(connection, partners)
    partners.routes[CREATE] = answer
    response = await client.get(f"{ROUTE}/uuid/u/doc_type/DS")
    assert response.status_code == 200
    assert response.content == page(f"Ошибка создания процесса подписания: {message}")


async def test_agreement_and_statement_identify_their_own_document(
    client: AsyncClient, connection: FakeConnection, partners: Partners
) -> None:
    contract = (
        "https://office.test/dict/report/mode/contract/is_pdf/false/for_view/false"
        "/client_request_id/7"
    )
    connection.handlers = {
        AGREEMENT_FUNCTION: [{"client_request_id": 7, "signed": False}],
        STATEMENT_FUNCTION: [
            {
                "client_request_document_id": 3,
                "client_request_id": 7,
                "document_url": "/documents/a.docx",
                "signed": False,
            }
        ],
    }
    partners.routes[contract] = httpx.Response(200, content=b"DOCX")
    partners.routes["https://office.test/documents/a.docx"] = httpx.Response(200, content=b"DOCX")
    partners.routes[CONVERT] = httpx.Response(200, json={"pdf": "UERG"})
    partners.routes[CREATE] = created()

    await client.get(f"{ROUTE}/uuid/u/doc_type/AGREEMENT/hash/h")
    payload = json.loads(partners.requests[-1].content)
    assert payload["ext_id"] == 7
    assert payload["file_name"] == "Договор_оказания_услуг_7.pdf"
    assert payload["meta_data"]["type_code"] == "CLIENT_SIGN"
    assert payload["meta_data"]["ds_id"] is None

    await client.get(f"{ROUTE}/uuid/u/doc_type/OTBASY_STATEMENT/hash/h")
    payload = json.loads(partners.requests[-1].content)
    assert payload["ext_id"] == 3
    assert payload["file_name"] == "Заявление_по_ОТБАСЫ_3.pdf"
    assert payload["meta_data"]["client_request_document_id"] == 3
    assert payload["back_url"].endswith(
        callback_key(CONFIG.callback_secret, "OTBASY_STATEMENT_SIGN", 3)
    )


async def test_mynca_token_does_not_go_without_tls_on_production(
    client: AsyncClient,
    connection: FakeConnection,
    partners: Partners,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app_config, "env", "prod")
    install_overrides(connection, partners, replace(CONFIG, mynca_base_url="http://mynca.test/api"))

    response = await client.get(f"{ROUTE}/uuid/u-1/doc_type/ds/hash/h-1")

    assert response.status_code == 503
    assert response.json() == {"detail": "CLIENT_SIGN_MYNCA_BASE_URL не https"}
    assert partners.requests == []
