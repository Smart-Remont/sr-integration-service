from fastapi import APIRouter, Request, Response

from src.php_compat.zend_request import php_raw_body, php_route

from ..openapi_examples import GET_CONSTRUCTIVES_RESPONSES, json_body
from ..php_contract import PhpBasicAuthDep, response_json_method_not_allowed
from .deps import GetConstructivesServiceDep

router = APIRouter()


@php_route(
    router,
    "/get-constructives",
    method="POST",
    other_methods=response_json_method_not_allowed,
    summary="Материалы заявки",
    description=(
        "Вызывает BI Group. Функция БД: `rest.bi_get_client_request_material_json`. "
        "`application_id` здесь номер заявки. Ошибка: HTTP 400, `data` с кодом `P400` или `P0xx`."
    ),
    openapi_extra=json_body({"application_id": 3219655}),
    responses=GET_CONSTRUCTIVES_RESPONSES,
)
async def get_constructives(
    request: Request,
    _: PhpBasicAuthDep,
    service: GetConstructivesServiceDep,
) -> Response:
    return await service.get_constructives(await php_raw_body(request))
