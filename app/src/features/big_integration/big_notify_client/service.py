from typing import TYPE_CHECKING

from fastapi import Response, status
from loguru import logger

from src.php_compat.json_codec import php_json_encode_pretty
from src.php_compat.pdo_errors import php_pdo_error_text

from ..errors import BigIntegrationDatabaseError
from ..php_contract import BigIntegrationRequestError, php_response

if TYPE_CHECKING:
    from .repo import BigNotifyClientRepository


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
    try:
        return raw_body.decode()
    except UnicodeDecodeError as exc:
        # PHP sent the bytes as they were and PostgreSQL refused them with this error.
        byte = raw_body[exc.start]
        raise BigNotifyClientError(
            php_pdo_error_text(
                "22021",
                "Character not in repertoire",
                f'invalid byte sequence for encoding "UTF8": 0x{byte:02x}',
            ),
        ) from exc
