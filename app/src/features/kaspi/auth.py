import secrets
from typing import Annotated

from fastapi import Header, HTTPException, status

from src.config import kaspi_proxy_config


async def require_kaspi_proxy_secret(
    secret: Annotated[str | None, Header(alias="X-Kaspi-Proxy-Secret")] = None,
) -> None:
    """Kaspi does not sign requests, so only nginx, with its address list, may reach the route."""
    expected = kaspi_proxy_config.secret
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Секрет прокси Kaspi не настроен",
        )
    # Bytes: compare_digest refuses a str with non-ASCII characters, and the header can have them.
    if secret is None or not secrets.compare_digest(secret.encode(), expected.encode()):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Запрос не от прокси")
