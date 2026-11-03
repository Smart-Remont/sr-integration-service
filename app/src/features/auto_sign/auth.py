from fastapi import HTTPException, status

from src.config import auto_sign_config, auto_sign_cron_auth_config, minio_config
from src.security import cron_basic_auth

# PHP checked nobody: any caller could trigger a company signature.
require_auto_sign_cron_auth = cron_basic_auth(auto_sign_cron_auth_config, "Auto sign cron")


def require_signing_config() -> None:
    if not auto_sign_config.signing_is_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Автоподпись не настроена",
        )


def require_upload_config() -> None:
    if not (auto_sign_config.upload_is_configured and minio_config.is_configured):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Загрузка подписанных документов не настроена",
        )
