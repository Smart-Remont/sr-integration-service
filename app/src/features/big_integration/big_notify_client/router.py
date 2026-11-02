from fastapi import APIRouter, Request, Response

from ..openapi_examples import BIG_NOTIFY_CLIENT_RESPONSES, json_body
from ..php_contract import PhpBasicAuthDep, php_route
from .deps import BigNotifyClientServiceDep
from .service import BigNotifyClientError

router = APIRouter()


async def _method_not_allowed(_: PhpBasicAuthDep) -> Response:
    raise BigNotifyClientError("Неподдерживаемый метод")


@php_route(
    router,
    "/big-notify-client",
    method="POST",
    other_methods=_method_not_allowed,
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
