"""PHP ``getConstructivesAction``: the materials of a request, by ``application_id``."""

from typing import TYPE_CHECKING

from fastapi import Response
from loguru import logger

from src.php_compat.json_codec import php_json_decode, php_json_decode_objects
from src.php_compat.pdo_errors import php_clean_error
from src.php_compat.pdo_values import pdo_bound_text
from src.php_compat.text import php_string

from ..errors import BigIntegrationDatabaseError
from ..php_contract import PhpResponseJsonError, php_bi_error_code, php_response_json_success

if TYPE_CHECKING:
    from .repo import GetConstructivesRepository


class GetConstructivesService:
    def __init__(self, repository: GetConstructivesRepository) -> None:
        self.repository = repository

    async def get_constructives(self, raw_body: bytes) -> Response:
        try:
            value = await self.repository.bi_get_client_request_material_json(
                _bound_application_id(php_json_decode(raw_body)),
            )
        except BigIntegrationDatabaseError as exc:
            logger.info("get-constructives: refused by the database")
            raise PhpResponseJsonError(exc.message, data=php_bi_error_code(exc.message)) from exc
        return php_response_json_success(
            None if value is None else php_json_decode_objects(str(value)),
        )


def _bound_application_id(payload: object) -> str | None:
    value = _application_id(payload)
    if value is None:
        return None
    return pdo_bound_text(php_string(value), _invalid_encoding)


def _application_id(payload: object) -> object | None:
    """``$a['application_id']`` as PHP 7.4 reads it from anything ``json_decode`` gave."""
    if isinstance(payload, dict):
        return payload.get("application_id")
    if isinstance(payload, str):
        # A string reads as its first byte, with a warning: the name is an illegal offset, so 0.
        return payload.encode()[:1].decode("utf-8", "surrogateescape")
    return None


def _invalid_encoding(pdo_message: str) -> BigIntegrationDatabaseError:
    return BigIntegrationDatabaseError(php_clean_error(pdo_message))
