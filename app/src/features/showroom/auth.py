from fastapi import HTTPException, status

from src.config import showroom_config, showroom_cron_auth_config
from src.security import cron_basic_auth

# PHP checked nobody: any caller could send the list to BI Group.
require_showroom_cron_auth = cron_basic_auth(showroom_cron_auth_config, "Showroom cron")


def require_showroom_bi_config() -> None:
    if not showroom_config.bi_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Адрес BI Group для шоурумов не настроен",
        )
