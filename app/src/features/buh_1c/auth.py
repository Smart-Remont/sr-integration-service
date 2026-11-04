from typing import Annotated

from fastapi import HTTPException, Request, status
from fastapi.security import HTTPBasicCredentials

from src.config import buh_1c_auth_config
from src.php_compat.basic_auth import php_basic_credentials
from src.security import verify_basic_credentials
from src.security.shown_login import shown_basic

from .errors import Buh1cUnauthorizedError

_NO_CREDENTIALS = HTTPBasicCredentials(username="", password="")


_SHOWN_LOGIN = shown_basic(
    "Buh1cBasic",
    "Basic как в PHP: учётка `BUH_1C_*`. Нет заголовка или он неверный: 401 с пустым телом. "
    "Нет настройки: 503.",
)


async def require_buh_1c_login(request: Request, _: Annotated[None, _SHOWN_LOGIN]) -> None:
    """The login PHP has written in ``buhAction``. No setting: 503, whatever the caller sent."""
    try:
        verify_basic_credentials(
            buh_1c_auth_config,
            await php_basic_credentials(request) or _NO_CREDENTIALS,
            api_name="1C exchange",
        )
    except HTTPException as exc:
        if exc.status_code != status.HTTP_401_UNAUTHORIZED:
            raise
        raise Buh1cUnauthorizedError from exc
