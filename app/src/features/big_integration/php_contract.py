"""Contract of BIG routes moved from PHP ``IntegrationController``: the same bytes as PHP.

Errors here are exceptions, so the request transaction rolls back (see "Данные и транзакции"
in CLAUDE.md); the handlers turn them into the response PHP gave.
"""

from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.security import HTTPBasic
from loguru import logger

from src.config import big_integration_auth_config
from src.php_compat.json_codec import php_json_encode, php_json_encode_pretty
from src.php_compat.pdo_errors import is_php_unhandled_error
from src.php_compat.responses import PHP_HTML_CONTENT_TYPE
from src.security import verify_basic_credentials

from .errors import BigIntegrationDatabaseError

_CONTENT_TYPE = "application/json"
_CACHE_HEADERS = {"Cache-Control": "no-cache, must-revalidate, max-age=0"}
_METHOD_NOT_ALLOWED = "Неподдерживаемый метод"
_http_basic = HTTPBasic(auto_error=False)


# As in ``_getBiErrorCode``, where P003 is missing from the list: it gives P400 too.
_PARTNER_ERROR_CODES = frozenset(
    f"P{number:03}" for number in (1, 2, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14)
)
_OTHER_ERROR_CODE = "P400"


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


class PhpResponseJsonError(BigIntegrationRequestError):
    """PHP ``response_json`` on an error: code 400, ``error.code``, pretty JSON."""

    def __init__(self, message: str, *, data: object | None = None) -> None:
        super().__init__(message)
        # An empty object when not passed: PHP filled ``data`` with ``new stdClass()`` beforehand.
        self.data = {} if data is None else data

    def to_response(self) -> Response:
        envelope = {
            "data": self.data,
            "response": False,
            "error": {"code": status.HTTP_400_BAD_REQUEST, "message": self.message},
        }
        return php_response(
            php_json_encode_pretty(envelope),
            status_code=status.HTTP_400_BAD_REQUEST,
        )


def php_bi_error_code(message: str) -> str:
    """``_getBiErrorCode``: the code a function raised at the start of its text, or P400."""
    code = message[:4]
    return code if code in _PARTNER_ERROR_CODES else _OTHER_ERROR_CODE


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


def php_response_json_success(data: object) -> Response:
    """PHP ``response_json`` on a success."""
    envelope = {"data": data, "response": True, "error": {"code": 0, "message": ""}}
    return php_response(php_json_encode_pretty(envelope))


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
    # PHP fills PHP_AUTH_USER only for this exact prefix, FastAPI ignores the case of the scheme.
    if credentials is None or not request.headers.get("authorization", "").startswith("Basic "):
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


async def response_json_method_not_allowed(_: PhpBasicAuthDep) -> Response:
    """PHP ``response_json`` routes do not answer 405: a method other than POST is a 400 error."""
    raise PhpResponseJsonError(_METHOD_NOT_ALLOWED)
