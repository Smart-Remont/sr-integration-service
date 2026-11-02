from fastapi import HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from src.config import buh_1c_auth_config
from src.security import verify_basic_credentials

from .errors import Buh1cUnauthorizedError

_http_basic = HTTPBasic(auto_error=False)
_NO_CREDENTIALS = HTTPBasicCredentials(username="", password="")


async def require_buh_1c_login(request: Request) -> None:
    """The login PHP has written in ``buhAction``. No setting: 503, whatever the caller sent."""
    try:
        verify_basic_credentials(
            buh_1c_auth_config,
            await _php_credentials(request),
            api_name="1C exchange",
        )
    except HTTPException as exc:
        if exc.status_code != status.HTTP_401_UNAUTHORIZED:
            raise
        raise Buh1cUnauthorizedError from exc


async def _php_credentials(request: Request) -> HTTPBasicCredentials:
    # PHP fills PHP_AUTH_USER only for this exact prefix, FastAPI ignores the case of the scheme.
    if not request.headers.get("authorization", "").startswith("Basic "):
        return _NO_CREDENTIALS
    try:
        return await _http_basic(request) or _NO_CREDENTIALS
    except HTTPException:
        return _NO_CREDENTIALS
