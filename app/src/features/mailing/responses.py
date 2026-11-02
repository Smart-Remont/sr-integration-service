"""Answers of the crons as PHP ``RestController`` gave them."""

from fastapi import Response

from src.php_compat.responses import PHP_SESSION_HEADERS

# ``RestController::init`` sets it with header(), and the cron actions keep it.
ACTION_CONTENT_TYPE = "application/json; text/html; charset=utf-8"
# ``http_response_code`` of the controller replaces it.
JSON_CONTENT_TYPE = "application/json"


def cron_response(body: bytes = b"") -> Response:
    return Response(
        content=body,
        headers={"Content-Type": ACTION_CONTENT_TYPE, **PHP_SESSION_HEADERS},
    )


def json_response(body: bytes) -> Response:
    return Response(
        content=body,
        headers={"Content-Type": JSON_CONTENT_TYPE, **PHP_SESSION_HEADERS},
    )
