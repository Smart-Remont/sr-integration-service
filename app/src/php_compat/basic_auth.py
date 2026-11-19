"""The login of Basic as PHP reads it."""

from fastapi import HTTPException, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials

_http_basic = HTTPBasic(auto_error=False)


async def php_basic_credentials(request: Request) -> HTTPBasicCredentials | None:
    """``PHP_AUTH_USER`` and ``PHP_AUTH_PW``. None when PHP would not fill them.

    PHP fills them only for this exact prefix, FastAPI ignores the case of the scheme. The order of
    checks around this call is the business of each route: PHP has written it differently in each.
    """
    if not request.headers.get("authorization", "").startswith("Basic "):
        return None
    try:
        return await _http_basic(request)
    except HTTPException:
        return None
