"""Contract of BIG routes moved from PHP ``IntegrationController``: the same bytes as PHP.

Errors here are exceptions, so the request transaction rolls back (see "Данные и транзакции"
in CLAUDE.md); the handlers turn them into the response PHP gave.
"""

from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.security import HTTPBasic
from loguru import logger

from src.config import big_integration_auth_config
from src.php_compat.json_codec import php_json_encode
from src.php_compat.pdo_errors import is_php_unhandled_error
from src.php_compat.responses import PHP_HTML_CONTENT_TYPE
from src.security import verify_basic_credentials

from .errors import BigIntegrationDatabaseError

_CONTENT_TYPE = "application/json"
_CACHE_HEADERS = {"Cache-Control": "no-cache, must-revalidate, max-age=0"}
_METHOD_NOT_ALLOWED = "Неподдерживаемый метод"
_http_basic = HTTPBasic(auto_error=False)


class BigIntegrationRequestError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status_code: int = status.HTTP_400_BAD_REQUEST,
    ) -> None:
        self.message = message
        self.status_code = status_code
        super().__init__(message)

    def to_response(self) -> Response:
        return php_error_response(self.message, status_code=self.status_code)


class BigIntegrationUnauthorizedError(Exception):
    pass


def php_response(content: bytes, *, status_code: int = status.HTTP_200_OK) -> Response:
    return Response(
        content=content,
        status_code=status_code,
        media_type=_CONTENT_TYPE,
        headers=_CACHE_HEADERS,
    )


def php_success_response(data: object) -> Response:
    envelope = {"data": data, "response": True, "error": {"message": ""}}
    return php_response(php_json_encode(envelope))


def php_error_response(
    message: str,
    *,
    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR,
) -> Response:
    envelope = {"data": None, "response": False, "error": {"message": message}}
    return php_response(php_json_encode(envelope), status_code=status_code)


def register_big_integration_handlers(app: FastAPI) -> None:
    async def request_error(_: Request, exc: BigIntegrationRequestError) -> Response:
        return exc.to_response()

    async def database_error(request: Request, exc: BigIntegrationDatabaseError) -> Response:
        # A function refusing on purpose is an ordinary answer, only a real failure is an error.
        level = "ERROR" if is_php_unhandled_error(exc.message) else "INFO"
        logger.log(
            level,
            "BIG route {path}: database error: {error}",
            path=request.url.path,
            error=exc.message,
        )
        return php_error_response(exc.message)

    async def unauthorized(_: Request, __: BigIntegrationUnauthorizedError) -> Response:
        # PHP sends no body here, and text/html is its default content type.
        return Response(
            status_code=status.HTTP_401_UNAUTHORIZED,
            media_type=PHP_HTML_CONTENT_TYPE,
            headers={**_CACHE_HEADERS, "WWW-Authenticate": 'Basic realm="Access denied"'},
        )

    app.add_exception_handler(BigIntegrationRequestError, request_error)
    app.add_exception_handler(BigIntegrationDatabaseError, database_error)
    app.add_exception_handler(BigIntegrationUnauthorizedError, unauthorized)


async def _require_php_basic_auth(request: Request) -> None:
    try:
        credentials = await _http_basic(request)
    except HTTPException as exc:
        raise BigIntegrationUnauthorizedError from exc
    if credentials is None:
        raise BigIntegrationUnauthorizedError
    try:
        verify_basic_credentials(
            big_integration_auth_config,
            credentials,
            api_name="Big integration",
        )
    except HTTPException as exc:
        if exc.status_code != status.HTTP_401_UNAUTHORIZED:
            raise
        raise BigIntegrationUnauthorizedError from exc


PhpBasicAuthDep = Annotated[None, Depends(_require_php_basic_auth)]


async def method_not_allowed(_: PhpBasicAuthDep) -> Response:
    return php_error_response(_METHOD_NOT_ALLOWED, status_code=status.HTTP_405_METHOD_NOT_ALLOWED)


def php_text(value: object) -> str:
    """How PHP turns a decoded JSON scalar into a SQL parameter."""
    if value is True:
        return "1"
    return str(value)
