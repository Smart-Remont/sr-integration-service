from fastapi import APIRouter, Request, Response

from src.php_compat.zend_request import php_route

from ..openapi_examples import BIG_NOTIFY_CLIENT_RESPONSES, json_body
from ..php_contract import PhpBasicAuthDep, response_json_method_not_allowed
from .deps import BigNotifyClientServiceDep

router = APIRouter()


@php_route(
    router,
    "/big-notify-client",
    method="POST",
    other_methods=response_json_method_not_allowed,
    summary="Уведомление клиенту от BI Group",
    description=(
        "Вызывает BI Group. Функция БД: `rest.big_notify_client`, она получает тело запроса "
        "текстом. Ошибка: HTTP 400 и `error.code` в ответе."
    ),
    openapi_extra=json_body({"phone": "77000000000", "text": "Текст уведомления"}),
    responses=BIG_NOTIFY_CLIENT_RESPONSES,
)
async def big_notify_client(
    request: Request,
    _: PhpBasicAuthDep,
    service: BigNotifyClientServiceDep,
) -> Response:
    return await service.big_notify_client(await request.body())
