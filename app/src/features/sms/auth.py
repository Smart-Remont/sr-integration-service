from fastapi import Depends, HTTPException, status

from src.config import kcell_config, sms_cron_auth_config
from src.security import basic_auth_dependency

# PHP checked nobody: any caller could trigger the sending of the queue.
require_sms_cron_auth = Depends(basic_auth_dependency(sms_cron_auth_config, api_name="SMS cron"))


def require_kcell_send_config() -> None:
    if not kcell_config.send_is_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Отправка через Kcell не настроена",
        )


def require_kcell_status_config() -> None:
    if not kcell_config.status_is_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Чтение статусов Kcell не настроено",
        )
