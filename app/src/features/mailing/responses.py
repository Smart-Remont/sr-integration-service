"""Answers of the crons as PHP ``RestController`` gave them."""

from fastapi import Response, status

from src.php_compat.responses import PHP_REST_CONTENT_TYPE, PHP_SESSION_HEADERS

# ``http_response_code`` of the controller replaces it.
JSON_CONTENT_TYPE = "application/json"


def cron_response(body: bytes = b"", *, status_code: int = status.HTTP_200_OK) -> Response:
    return Response(
        content=body,
        status_code=status_code,
        headers={"Content-Type": PHP_REST_CONTENT_TYPE, **PHP_SESSION_HEADERS},
    )


def json_response(body: bytes) -> Response:
    return Response(
        content=body,
        headers={"Content-Type": JSON_CONTENT_TYPE, **PHP_SESSION_HEADERS},
    )
