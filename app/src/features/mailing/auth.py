from fastapi import HTTPException, status

from src.config import mailing_config, mailing_cron_auth_config
from src.security import cron_basic_auth

# PHP checked nobody: any caller could trigger the sending of the queue.
require_mailing_cron_auth = cron_basic_auth(mailing_cron_auth_config, "Mailing cron")


def require_queue_config() -> None:
    if not mailing_config.queue_is_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Отправка писем не настроена",
        )
