"""Contract of BIG routes moved from PHP ``IntegrationController``: the same bytes as PHP.

Errors here are exceptions, so the request transaction rolls back (see "Данные и транзакции"
in CLAUDE.md); the handlers turn them into the response PHP gave.
"""

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.security import HTTPBasic
from loguru import logger

from src.config import big_integration_auth_config
from src.security import verify_basic_credentials
from src.shared.php_json import php_json_encode

from .errors import BigIntegrationDatabaseError

_CONTENT_TYPE = "application/json"
_CACHE_HEADERS = {"Cache-Control": "no-cache, must-revalidate, max-age=0"}
_METHOD_NOT_ALLOWED = "Неподдерживаемый метод"
_FORM_CONTENT_TYPES = ("application/x-www-form-urlencoded", "multipart/form-data")
_ALL_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
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
        logger.error(
            "BIG route {path}: database error: {error}",
            path=request.url.path,
            error=exc.message,
        )
        return php_error_response(exc.message)

    async def unauthorized(_: Request, __: BigIntegrationUnauthorizedError) -> Response:
        # PHP sends no body here, and text/html is its default content type.
        return Response(
            status_code=status.HTTP_401_UNAUTHORIZED,
            media_type="text/html; charset=UTF-8",
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


def php_route(
    router: APIRouter,
    path: str,
    *,
    method: str,
    other_methods: Callable[..., Any] | None = None,
    **docs: Any,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register a route the way the Zend router served it.

    Scalar shows ``method`` on ``path``. As in PHP, the address also works with a trailing slash
    and ``/key/value`` pairs, and other HTTP methods reach ``other_methods`` (the same endpoint
    when PHP did not check the method).
    """

    def register(endpoint: Callable[..., Any]) -> Callable[..., Any]:
        with_tail = f"{path}/{{tail:path}}"
        others = [item for item in _ALL_METHODS if item != method]
        router.add_api_route(path, endpoint, methods=[method], **docs)
        router.add_api_route(with_tail, endpoint, methods=[method], include_in_schema=False)
        for route_path in (path, with_tail):
            router.add_api_route(
                route_path,
                other_methods or endpoint,
                methods=others,
                include_in_schema=False,
            )
        return endpoint

    return register


async def read_param(request: Request, name: str) -> str:
    """``_getParam``: ``/key/value`` pairs of the address, the query string, then the POST form."""
    value = _address_pairs(request).get(name)
    if value is None:
        value = request.query_params.get(name)
    if value is None and request.method == "POST":
        value = await _form_value(request, name)
    return value or ""


def _address_pairs(request: Request) -> dict[str, str]:
    parts = str(request.path_params.get("tail", "")).split("/")
    return dict(zip(parts[::2], parts[1::2], strict=False))


async def _form_value(request: Request, name: str) -> str | None:
    if not request.headers.get("content-type", "").startswith(_FORM_CONTENT_TYPES):
        return None
    value = (await request.form()).get(name)
    return value if isinstance(value, str) else None


def zero_to_null(value: str) -> str | None:
    """PHP ``zeroToNull``: an empty value and ``"0"`` mean "not passed"."""
    return None if value in ("", "0") else value


def is_php_empty(value: object) -> bool:
    """The controller's ``is_null or empty or strlen(trim(...)) === 0``."""
    if isinstance(value, str):
        return value.strip(" \t\n\r\0\x0b") in ("", "0")
    # trim() of an array gives null, so an array of any size counts as empty.
    return isinstance(value, list | dict) or not value


def php_text(value: object) -> str:
    """How PHP turns a decoded JSON scalar into a SQL parameter."""
    if value is True:
        return "1"
    return str(value)
