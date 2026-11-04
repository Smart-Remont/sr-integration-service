"""PHP ``IntegrationController::albatoMetaLeadAction``. Any HTTP method and Zend address form.

The token comes before everything: with a wrong token another method gets 401 and not 405.
"""

from fastapi import APIRouter, Depends, Request, Response

from src.php_compat.zend_request import php_raw_body, php_route

from .auth import require_albato_meta_token
from .deps import AlbatoMetaServiceDep
from .openapi_examples import ALBATO_META_LEAD_REQUEST, ALBATO_META_LEAD_RESPONSES

router = APIRouter(tags=["Albato Meta"], dependencies=[Depends(require_albato_meta_token)])


@php_route(
    router,
    "/albato-meta-lead",
    method="POST",
    summary="Лид Meta через Albato",
    description=(
        "Вызывает Albato. Функции БД: `public.get_setting_value_by_code` (токен), "
        "`crm.request_from_albato_meta`, она создаёт сделку. Значения тела проходят фильтр PHP "
        "`filter_arr`. Токен лежит в адресе: в nginx на этом адресе `access_log off`."
    ),
    openapi_extra=ALBATO_META_LEAD_REQUEST,
    responses=ALBATO_META_LEAD_RESPONSES,
)
async def albato_meta_lead(request: Request, service: AlbatoMetaServiceDep) -> Response:
    return await service.create_lead(
        method=request.method,
        raw_body=await php_raw_body(request),
    )
