"""Answers as PHP sent them. Names of files and redirect targets are raw UTF-8, as ``header()`` sent."""

from fastapi import Response

from src.php_compat.json_codec import php_json_encode_default
from src.php_compat.responses import PHP_HTML_CONTENT_TYPE, PHP_SESSION_HEADERS, php_html_response

PDF_CONTENT_TYPE = "application/pdf"
CMS_CONTENT_TYPE = "application/pkcs7-signature"

_JSON_CONTENT_TYPE = "application/json"
_FOUND = 302
_FILE_HEADERS = {
    "Content-Description": "File Transfer",
    "Content-Transfer-Encoding": "binary",
    "Expires": "0",
    "Cache-Control": "must-revalidate, post-check=0, pre-check=0",
    "Pragma": "public",
}


def php_text_response(text: str) -> Response:
    return php_html_response(text.encode("utf-8", "surrogateescape"))


def json_answer(answer: dict[str, object], status_code: int = 200) -> Response:
    # Invalid UTF-8 makes json_encode return false, and PHP echoes nothing.
    return Response(
        content=php_json_encode_default(answer).encode(),
        status_code=status_code,
        headers={"Content-Type": _JSON_CONTENT_TYPE, **PHP_SESSION_HEADERS},
    )


def json_body_response(body: str, status_code: int) -> Response:
    return Response(
        content=body.encode("utf-8", "surrogateescape"),
        status_code=status_code,
        headers={"Content-Type": _JSON_CONTENT_TYPE, **PHP_SESSION_HEADERS},
    )


def redirect_response(url: str) -> Response:
    """``Zend_Controller_Action_Helper_Redirector::gotoUrl``: an address without a scheme gets a ``/``."""
    target = url.replace("\n", "").replace("\r", "")
    if not _has_lowercase_scheme(target):
        target = "/" + target.lstrip("/")
    response = Response(
        status_code=_FOUND,
        headers={"Content-Type": PHP_HTML_CONTENT_TYPE, **PHP_SESSION_HEADERS},
    )
    _append_raw_header(response, b"location", target)
    return response


def file_response(
    content: bytes,
    content_type: str,
    file_name: str,
    *,
    send_length: bool = True,
) -> Response:
    response = Response(
        content=content,
        headers={"Content-Type": content_type, **_FILE_HEADERS},
    )
    _append_raw_header(response, b"content-disposition", f"attachment; filename={file_name}")
    if not send_length:
        response.raw_headers = [
            header for header in response.raw_headers if header[0] != b"content-length"
        ]
    return response


def _append_raw_header(response: Response, name: bytes, value: str) -> None:
    response.raw_headers.append((name, value.encode("utf-8", "surrogateescape")))


def _has_lowercase_scheme(url: str) -> bool:
    scheme, separator, _ = url.partition("://")
    return bool(separator) and scheme.isascii() and scheme.isalpha() and scheme.islower()
