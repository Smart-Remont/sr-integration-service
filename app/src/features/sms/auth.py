from fastapi import HTTPException, status

from src.config import kcell_config, sms_cron_auth_config
from src.security import cron_basic_auth

# PHP checked nobody: any caller could trigger the sending of the queue.
require_sms_cron_auth = cron_basic_auth(sms_cron_auth_config, "SMS cron")


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
