import secrets

from fastapi import Request, status
from loguru import logger

from src.database.deps import CommitBeforeResponseConnectionDep
from src.php_compat.pdo_errors import php_error_state
from src.php_compat.text import php_trim
from src.php_compat.zend_request import PhpParam, php_raw_body, read_params

from .errors import AlbatoMetaError
from .repo import AlbatoMetaRepository

_PARAM_NAME = "token"
_NOT_SET_TEXT = "Не задан токен интеграции"
_WRONG_VALUE_TEXT = "Неверный токен"


async def require_albato_meta_token(
    request: Request,
    connection: CommitBeforeResponseConnectionDep,
) -> None:
    """The token of PHP: a value in the address, checked against ``ALBATO_META_TOKEN`` in the database.

    Unlike PHP, which answers 500, no value means 503, as on every route of this service.
    """
    expected = await _expected_token(AlbatoMetaRepository(connection=connection))
    if not expected:
        raise AlbatoMetaError(_NOT_SET_TEXT, status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
    await php_raw_body(request)
    params = await read_params(request)
    if not _token_matches(params.get(_PARAM_NAME), expected):
        raise AlbatoMetaError(_WRONG_VALUE_TEXT, status_code=status.HTTP_401_UNAUTHORIZED)


async def _expected_token(repository: AlbatoMetaRepository) -> str:
    try:
        return php_trim(await repository.access_token() or "")
    except Exception as exc:
        # ``scalarSP`` gives null on an error, and PHP goes on as if the setting were empty.
        logger.error("albato-meta-lead: token not read, state {state}", state=php_error_state(exc))
        return ""


def _token_matches(token: PhpParam | None, expected: str) -> bool:
    # A parameter given twice or as a[] is an array, and ``!==`` refuses it.
    if not isinstance(token, str):
        return False
    return secrets.compare_digest(token.encode("utf-8", "surrogateescape"), expected.encode())
