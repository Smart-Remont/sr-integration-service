"""Headers PHP adds to every answer of a controller that opens a session."""

from fastapi import Response

PHP_SESSION_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate",
    "Expires": "Thu, 19 Nov 1981 08:52:00 GMT",
    "Pragma": "no-cache",
}
PHP_HTML_CONTENT_TYPE = "text/html; charset=UTF-8"


def php_html_response(body: bytes = b"") -> Response:
    return Response(
        content=body,
        headers={"Content-Type": PHP_HTML_CONTENT_TYPE, **PHP_SESSION_HEADERS},
    )
