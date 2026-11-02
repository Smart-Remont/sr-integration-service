from fastapi import Depends, HTTPException, status

from src.config import mailing_config, mailing_cron_auth_config
from src.security import basic_auth_dependency

# PHP checked nobody: any caller could trigger the sending of the queue.
require_mailing_cron_auth = Depends(
    basic_auth_dependency(mailing_cron_auth_config, api_name="Mailing cron"),
)


def require_queue_config() -> None:
    if not mailing_config.queue_is_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Отправка писем не настроена",
        )


def require_error_queue_config() -> None:
    if not mailing_config.error_queue_is_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Отправка писем об ошибках БД не настроена",
        )


def require_provider_files_config() -> None:
    if not mailing_config.office_public_dir:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Папка файлов Office не настроена",
        )
