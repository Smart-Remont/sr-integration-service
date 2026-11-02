"""PHP ``bigappFormAction`` and ``bigCrmFormAction``: a lead from a BI Group form.

Function refusals carry only fixed texts, but a failure of the database can quote the lead, so the
log gets the state and not the text.
"""

import math
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from fastapi import Response
from loguru import logger

from src.php_compat.input_filter import php_filter_values
from src.php_compat.json_codec import (
    php_json_decode,
    php_json_encode_pretty,
    php_json_encode_unescaped,
)
from src.php_compat.pdo_errors import is_php_unhandled_error, php_error_state

from ..errors import BigIntegrationDatabaseError
from ..php_contract import PhpResponseJsonError, php_bi_error_code, php_response_json_success

if TYPE_CHECKING:
    from .repo import RequestFormsRepository


class RequestFormsService:
    def __init__(self, repository: RequestFormsRepository) -> None:
        self.repository = repository

    async def bigapp_form(self, raw_body: bytes) -> Response:
        return await _create_lead(
            "bigapp-form",
            self.repository.request_from_bigapp,
            raw_body,
        )

    async def big_crm_form(self, raw_body: bytes) -> Response:
        return await _create_lead(
            "big-crm-form",
            self.repository.request_from_bigcrm,
            raw_body,
        )


async def _create_lead(
    route: str,
    create: Callable[[str], Awaitable[object | None]],
    raw_body: bytes,
) -> Response:
    try:
        value = await create(_function_text(php_json_decode(raw_body)))
    except BigIntegrationDatabaseError as exc:
        level = "ERROR" if is_php_unhandled_error(exc.message) else "INFO"
        logger.log(
            level, "{route}: refused, state {state}", route=route, state=php_error_state(exc)
        )
        raise PhpResponseJsonError(exc.message, data=php_bi_error_code(exc.message)) from exc
    return php_response_json_success(value)


def _function_text(decoded: object) -> str:
    if isinstance(decoded, list | dict):
        return php_json_encode_pretty(php_filter_values(decoded)).decode()
    # PHP warns here and encodes the value as it is. The function refuses all of these anyway.
    if isinstance(decoded, float) and not math.isfinite(decoded):
        return ""
    return php_json_encode_unescaped(decoded)
