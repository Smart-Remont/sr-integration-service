from typing import TYPE_CHECKING

from fastapi import Response
from loguru import logger

from src.php_compat.pdo_errors import php_clean_error, php_error_state
from src.php_compat.pdo_values import pdo_bound_text
from src.php_compat.text import PHP_TRIM_BYTES

from ..errors import BigIntegrationDatabaseError
from ..php_contract import PhpResponseJsonError, php_response_json_success

if TYPE_CHECKING:
    from .repo import BigNotifyClientRepository


class BigNotifyClientService:
    def __init__(self, repository: BigNotifyClientRepository) -> None:
        self.repository = repository

    async def big_notify_client(self, raw_body: bytes) -> Response:
        try:
            value = await self.repository.big_notify_client(_body_text(raw_body))
        except BigIntegrationDatabaseError as exc:
            logger.error(
                "big-notify-client: database error, state {state}", state=php_error_state(exc)
            )
            raise PhpResponseJsonError(exc.message) from exc
        return php_response_json_success(value)


def _body_text(raw_body: bytes) -> str:
    """The text PDO bound: ``getRawBody`` gives ``false`` for a blank body, which PDO sends as ''."""
    if not raw_body.strip(PHP_TRIM_BYTES):
        return ""
    text = pdo_bound_text(
        raw_body.decode("utf-8", "surrogateescape"),
        lambda pdo_message: PhpResponseJsonError(php_clean_error(pdo_message)),
    )
    return text or ""
