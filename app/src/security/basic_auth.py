from collections.abc import Callable
from secrets import compare_digest
from typing import Annotated, Protocol

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

_http_basic = HTTPBasic()


class BasicCredentialsConfig(Protocol):
    username: str
    password: str


def basic_auth_dependency(
    config: BasicCredentialsConfig,
    *,
    api_name: str,
) -> Callable[[HTTPBasicCredentials], None]:
    """FastAPI dependency checking HTTP Basic credentials against ``config``.

    Not configured credentials mean 503: the service must never fall open.
    Comparison is constant-time.
    """

    def verify(credentials: Annotated[HTTPBasicCredentials, Depends(_http_basic)]) -> None:
        if not (config.username and config.password):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"{api_name} API credentials are not configured.",
            )
        username_ok = compare_digest(credentials.username.encode(), config.username.encode())
        password_ok = compare_digest(credentials.password.encode(), config.password.encode())
        if not (username_ok and password_ok):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials",
                headers={"WWW-Authenticate": "Basic"},
            )

    return verify
