from collections.abc import Callable
from secrets import compare_digest
from typing import Annotated, Any, Protocol

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
    """FastAPI dependency checking HTTP Basic credentials against ``config``."""

    def verify(credentials: Annotated[HTTPBasicCredentials, Depends(_http_basic)]) -> None:
        verify_basic_credentials(config, credentials, api_name=api_name)

    return verify


def cron_basic_auth(config: BasicCredentialsConfig, name: str) -> Any:
    """The router dependency of a cron. Its name is in the text of the 503 of an empty setting."""
    return Depends(basic_auth_dependency(config, api_name=name))


def verify_basic_credentials(
    config: BasicCredentialsConfig,
    credentials: HTTPBasicCredentials,
    *,
    api_name: str,
) -> None:
    """Not configured credentials mean 503: the service must never fall open.

    Comparison is constant-time.
    """
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
