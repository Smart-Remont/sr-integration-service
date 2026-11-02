from fastapi import FastAPI, Request, Response

from src.php_compat.json_codec import php_json_encode_default
from src.php_compat.responses import PHP_SESSION_HEADERS

CONTENT_TYPE = "application/json; charset=utf-8"


class AlbatoMetaError(Exception):
    """A refusal in PHP's words. An exception, so that the request transaction rolls back."""

    def __init__(self, message: str, *, status_code: int) -> None:
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def albato_meta_response(content: dict[str, object], *, status_code: int) -> Response:
    return Response(
        content=php_json_encode_default(content).encode(),
        status_code=status_code,
        headers={"Content-Type": CONTENT_TYPE, **PHP_SESSION_HEADERS},
    )


def register_albato_meta_handlers(app: FastAPI) -> None:
    async def refused(_: Request, exc: AlbatoMetaError) -> Response:
        return albato_meta_response({"error": exc.message}, status_code=exc.status_code)

    app.add_exception_handler(AlbatoMetaError, refused)
