from fastapi import APIRouter, Request, Response

from src.php_compat.zend_request import php_raw_body, php_route

from ..openapi_examples import REQUEST_FORM_RESPONSES, json_body
from ..php_contract import PhpBasicAuthDep, response_json_method_not_allowed
from .deps import RequestFormsServiceDep

router = APIRouter()


@php_route(
    router,
    "/bigapp-form",
    method="POST",
    other_methods=response_json_method_not_allowed,
    summary="Лид из приложения BI Group",
    description=(
        "Вызывает BI Group. Функция БД: `crm.request_from_bigapp`, она создаёт минимальный лид. "
        "Значения тела проходят фильтр PHP `filter_arr`. Ошибка: HTTP 400, `data` с кодом "
        "`P400` или `P0xx`."
    ),
    openapi_extra=json_body({"client_fio": "Иванов Иван", "client_phone": "77000000000"}),
    responses=REQUEST_FORM_RESPONSES,
)
async def bigapp_form(
    request: Request,
    _: PhpBasicAuthDep,
    service: RequestFormsServiceDep,
) -> Response:
    return await service.bigapp_form(await php_raw_body(request))


@php_route(
    router,
    "/big-crm-form",
    method="POST",
    other_methods=response_json_method_not_allowed,
    summary="Лид из CRM BI Group",
    description=(
        "Вызывает BI Group. Функция БД: `crm.request_from_bigcrm`, она создаёт минимальный лид. "
        "Значения тела проходят фильтр PHP `filter_arr`. Ошибка: HTTP 400, `data` с кодом "
        "`P400` или `P0xx`."
    ),
    openapi_extra=json_body(
        {
            "client_fio": "Иванов Иван",
            "origin_code": "BIG_OP",
            "client_phones": [{"phone_number": "77000000000", "is_main": 1}],
        },
    ),
    responses=REQUEST_FORM_RESPONSES,
)
async def big_crm_form(
    request: Request,
    _: PhpBasicAuthDep,
    service: RequestFormsServiceDep,
) -> Response:
    return await service.big_crm_form(await php_raw_body(request))
