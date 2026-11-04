import re
from typing import TYPE_CHECKING

from fastapi import Response, status

from src.php_compat.pdo_errors import php_pdo_error_text
from src.php_compat.pdo_values import libpq_c_string
from src.php_compat.text import zero_to_null

from ..php_contract import BigIntegrationRequestError, php_success_response

if TYPE_CHECKING:
    from .repo import DduLookupRepository

_INTEGER = re.compile(r"\s*[+-]?[0-9]+\s*")
_INT4_MAX = 2**31 - 1


class DduLookupService:
    def __init__(self, repository: DduLookupRepository) -> None:
        self.repository = repository

    async def request_info(self, deal_id: str, application_id: str, order_id: str) -> Response:
        row = await self.repository.ddu_request_info(
            zero_to_null(deal_id),
            zero_to_null(application_id),
            zero_to_null(order_id),
        )
        return php_success_response(row)

    async def request_status_info(
        self,
        deal_id: str,
        application_id: str,
        order_id: str,
    ) -> Response:
        row = await self.repository.ddu_request_and_status_info(
            zero_to_null(deal_id),
            zero_to_null(application_id),
            zero_to_null(order_id),
        )
        return php_success_response(row)

    async def ddu_request_info(self, client_request_id: str) -> Response:
        row = await self.repository.ddu_request_info_by_client_request(
            _client_request_id(client_request_id),
        )
        return php_success_response(row)

    async def ddu_flat_info(self, flat_guid: str) -> Response:
        row = await self.repository.ddu_flat_info(zero_to_null(flat_guid))
        return php_success_response(row)


def _client_request_id(value: str) -> int | None:
    text = zero_to_null(value)
    if text is None:
        return None
    text = libpq_c_string(text)
    # PHP sent the text to PostgreSQL; asyncpg needs a number, so the cast errors are made here.
    if not _INTEGER.fullmatch(text):
        raise _cast_error(
            "22P02",
            "Invalid text representation",
            f'invalid input syntax for type integer: "{text}"',
        )
    number = int(text)
    if abs(number) > _INT4_MAX:
        raise _cast_error(
            "22003",
            "Numeric value out of range",
            f'value "{text}" is out of range for type integer',
        )
    return number


def _cast_error(sqlstate: str, state_name: str, text: str) -> BigIntegrationRequestError:
    return BigIntegrationRequestError(
        php_pdo_error_text(sqlstate, state_name, text),
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )
