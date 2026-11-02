"""PHP ``albatoMetaLeadAction``, the same checks in the same order.

A failure of the database can quote the lead, so the log gets the state and not the text.
"""

from fastapi import Response, status
from loguru import logger

from src.php_compat.input_filter import php_filter_values
from src.php_compat.json_codec import php_json_decode, php_json_encode_unescaped
from src.php_compat.pdo_errors import is_php_unhandled_error, php_error_state, php_error_text
from src.php_compat.text import php_is_truthy, php_string, php_trim

from .errors import AlbatoMetaError, albato_meta_response
from .repo import AlbatoMetaRepository

_POST = "POST"
_METHOD_NOT_ALLOWED = "Метод не поддерживается"
_INVALID_BODY = "Пустое или невалидное тело запроса"
_NAME_REQUIRED = "Поле name обязательно"
_PHONE_REQUIRED = "Поле phone обязательно"
_NOT_CREATED = "Не удалось создать лид"


class AlbatoMetaService:
    def __init__(self, repository: AlbatoMetaRepository) -> None:
        self.repository = repository

    async def create_lead(self, *, method: str, raw_body: bytes) -> Response:
        if method != _POST:
            raise AlbatoMetaError(
                _METHOD_NOT_ALLOWED,
                status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
            )
        lead = php_json_decode(raw_body)
        if not isinstance(lead, list | dict):
            raise _bad_request(_INVALID_BODY)
        if _is_blank(lead, "name"):
            raise _bad_request(_NAME_REQUIRED)
        if _is_blank(lead, "phone"):
            raise _bad_request(_PHONE_REQUIRED)
        payload = php_json_encode_unescaped(php_filter_values(lead))
        try:
            deal_id = await self.repository.request_from_albato_meta(payload)
        except Exception as exc:
            raise _database_refusal(exc) from exc
        return albato_meta_response(
            {"status": "ok", "crm_deal_id": deal_id},
            status_code=status.HTTP_201_CREATED,
        )


def _is_blank(lead: list[object] | dict[str, object], field: str) -> bool:
    value = lead.get(field) if isinstance(lead, dict) else None
    return php_trim(php_string(value)) == ""


def _bad_request(message: str) -> AlbatoMetaError:
    return AlbatoMetaError(message, status_code=status.HTTP_400_BAD_REQUEST)


def _database_refusal(exc: Exception) -> AlbatoMetaError:
    message = php_error_text(exc)
    level = "ERROR" if is_php_unhandled_error(message) else "INFO"
    logger.log(level, "albato-meta-lead: refused, state {state}", state=php_error_state(exc))
    return AlbatoMetaError(
        message if php_is_truthy(message) else _NOT_CREATED,
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )
