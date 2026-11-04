import secrets
from typing import Annotated

from fastapi import Header, HTTPException, status

from src.config import kaspi_proxy_config
from src.security.shown_login import shown_header

_SHOWN_LOGIN = shown_header(
    "KaspiProxyHeader",
    "X-Kaspi-Proxy-Secret",
    "Ставит nginx, у которого список адресов Kaspi: сам Kaspi запросы не подписывает. Сверка с "
    "`KASPI_PROXY_SECRET`. Нет или неверный: 403. Нет настройки: 503.",
)


async def require_kaspi_proxy_secret(
    _: Annotated[None, _SHOWN_LOGIN],
    secret: Annotated[
        str | None, Header(alias="X-Kaspi-Proxy-Secret", include_in_schema=False)
    ] = None,
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
