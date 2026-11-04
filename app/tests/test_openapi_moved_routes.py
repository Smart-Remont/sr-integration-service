"""What Scalar shows for the routes moved from PHP. The answers themselves are in the tests of each route."""

import json

import pytest

from src.main import app

_METHODS = {"get", "post", "put", "delete", "patch"}
_MOVED_PREFIXES = (
    "/api/kaspi/",
    "/api/sms/",
    "/api/auto_sign/",
    "/api/client_sign/",
    "/api/albato/",
    "/api/showroom/",
    "/api/buh_1c/",
    "/api/mailing/",
)
_MOVED_BIG = {
    "request-info",
    "request-status-info",
    "ddu-request-info",
    "ddu-flat-info",
    "request-constructives",
    "remont-preset",
    "remont-preset-list-v2",
    "big-notify-client",
    "bigapp-form",
    "big-crm-form",
    "get-constructives",
}
# A page that is always the same and shows no data: there is no login to show.
_NO_LOGIN = {"/api/client_sign/old-sign-doc-list"}


def _moved_operations() -> list[tuple[str, str, dict]]:
    found = []
    for path, operations in app.openapi()["paths"].items():
        moved = (
            path.startswith(_MOVED_PREFIXES)
            or path.removeprefix("/api/big_integration/") in _MOVED_BIG
        )
        if moved:
            found += [(path, method, op) for method, op in operations.items() if method in _METHODS]
    return found


def test_all_moved_routes_are_there() -> None:
    assert len(_moved_operations()) >= 30


@pytest.mark.parametrize(("path", "method", "operation"), _moved_operations())
def test_moved_route_shows_login_description_and_answers(
    path: str, method: str, operation: dict
) -> None:
    assert operation.get("summary"), path
    assert operation.get("description"), path
    assert operation.get("tags"), path
    assert (
        "200" in operation["responses"]
        or "201" in operation["responses"]
        or "302" in operation["responses"]
    ), path
    if path in _NO_LOGIN:
        assert "входа нет" in operation["description"]
    else:
        assert operation.get("security"), f"{method} {path}: no login in the document"


def test_login_schemes_say_what_they_are() -> None:
    schemes = app.openapi()["components"]["securitySchemes"]
    assert schemes["BiGroupBasic"]["scheme"] == "basic"
    assert schemes["Buh1cBasic"]["scheme"] == "basic"
    assert schemes["KaspiProxyHeader"] == {
        **schemes["KaspiProxyHeader"],
        "type": "apiKey",
        "in": "header",
        "name": "X-Kaspi-Proxy-Secret",
    }
    assert schemes["AlbatoToken"] == {
        **schemes["AlbatoToken"],
        "type": "apiKey",
        "in": "query",
        "name": "token",
    }


def test_frozen_routes_show_no_new_login() -> None:
    paths = app.openapi()["paths"]
    for route in (
        "request-create-v3",
        "request-event-v3",
        "repair-pack-prices",
        "ddu-flat-info-multiple",
        "ddu-resident-agreement-status",
    ):
        assert paths[f"/api/big_integration/{route}"]["post"]["security"] == [{"HTTPBasic": []}]


@pytest.mark.parametrize(
    ("path", "content_type"),
    [
        ("/api/client_sign/download", "application/pdf"),
        ("/api/client_sign/download-signed", "application/pdf"),
        ("/api/client_sign/download-cms", "application/pkcs7-signature"),
        ("/api/client_sign/download-agreement", "application/pdf"),
        ("/api/client_sign/download-ds", "application/pdf"),
    ],
)
def test_downloads_say_the_type_of_file(path: str, content_type: str) -> None:
    content = app.openapi()["paths"][path]["get"]["responses"]["200"]["content"]
    assert content[content_type]["schema"] == {"type": "string", "format": "binary"}


@pytest.mark.parametrize(
    ("path", "code"),
    [
        ("/api/showroom/showroom-info-list", "200"),
        ("/api/mailing/fill-send-email", "200"),
        ("/api/mailing/send-email-new", "200"),
        ("/api/mailing/send-email-new", "503"),
    ],
)
def test_crons_have_examples_of_the_answer(path: str, code: str) -> None:
    content = next(iter(app.openapi()["paths"][path]["get"]["responses"][code]["content"].values()))
    assert content.get("examples") or content.get("example")


def test_perform_sign_shows_both_answers() -> None:
    responses = app.openapi()["paths"]["/api/client_sign/perform-sign"]["get"]["responses"]
    assert responses["302"]["headers"]["Location"]["example"].endswith(
        "2b1f3c1e-5a8e-4f7c-9d11-4f7a9b1c2d3e"
    )
    assert "Ошибка" in responses["200"]["content"]["text/html"]["example"]


def test_paid_requests_says_it_creates_remonts() -> None:
    operation = app.openapi()["paths"]["/api/mailing/paid-client-request-to-remont"]["get"]
    assert "создаёт ремонты" in operation["description"]
    assert operation["tags"] == ["Mailing and service crons"]


def test_every_tag_of_the_document_is_described() -> None:
    document = app.openapi()
    described = {tag["name"] for tag in document["tags"]}
    used = {
        tag
        for path in document["paths"].values()
        for op in path.values()
        for tag in op.get("tags", [])
    }
    assert used <= described


def test_examples_have_no_real_looking_secrets() -> None:
    text = json.dumps(app.openapi(), ensure_ascii=False)
    for forbidden in ("Authorization: Basic ", "Bearer ", "password="):
        assert forbidden not in text
