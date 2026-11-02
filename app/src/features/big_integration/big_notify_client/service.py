from typing import TYPE_CHECKING

from fastapi import Response, status
from loguru import logger

from src.php_compat.json_codec import php_json_encode_pretty
from src.php_compat.pdo_errors import php_clean_error
from src.php_compat.pdo_values import pdo_bound_text

from ..errors import BigIntegrationDatabaseError
from ..php_contract import BigIntegrationRequestError, php_response

if TYPE_CHECKING:
    from .repo import BigNotifyClientRepository

_PHP_TRIM_BYTES = b" \t\n\r\0\x0b"


class BigNotifyClientError(BigIntegrationRequestError):
    """This route alone used PHP ``response_json``: ``error.code`` and an empty ``data`` object."""

    def to_response(self) -> Response:
        envelope = {
            "data": {},
            "response": False,
            "error": {"code": status.HTTP_400_BAD_REQUEST, "message": self.message},
        }
        return php_response(
            php_json_encode_pretty(envelope),
            status_code=status.HTTP_400_BAD_REQUEST,
        )


class BigNotifyClientService:
    def __init__(self, repository: BigNotifyClientRepository) -> None:
        self.repository = repository

    async def big_notify_client(self, raw_body: bytes) -> Response:
        try:
            value = await self.repository.big_notify_client(_body_text(raw_body))
        except BigIntegrationDatabaseError as exc:
            logger.error("big-notify-client: database error: {error}", error=exc.message)
            raise BigNotifyClientError(exc.message) from exc
        envelope = {"data": value, "response": True, "error": {"code": 0, "message": ""}}
        return php_response(php_json_encode_pretty(envelope))


def _body_text(raw_body: bytes) -> str:
    """The text PDO bound: ``getRawBody`` gives ``false`` for a blank body, which PDO sends as ''."""
    if not raw_body.strip(_PHP_TRIM_BYTES):
        return ""
    text = pdo_bound_text(
        raw_body.decode("utf-8", "surrogateescape"),
        lambda pdo_message: BigNotifyClientError(php_clean_error(pdo_message)),
    )
    return text or ""
