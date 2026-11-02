from fastapi import APIRouter, Request, Response

from src.shared.php_json import php_json_decode

from ..openapi_examples import REQUEST_CONSTRUCTIVES_RESPONSES, json_body
from ..php_contract import PhpBasicAuthDep, method_not_allowed, php_route
from .deps import RequestConstructivesServiceDep

router = APIRouter()


@php_route(
    router,
    "/request-constructives",
    method="POST",
    other_methods=method_not_allowed,
    summary="Материалы и наполнение заявки",
    description=(
        "Вызывает BI Group. Функции БД: `rest.ddu_client_material__read`, "
        "`rest.ddu_client_filling__read`, `rest.ddu_request_full_info`."
    ),
    openapi_extra=json_body({"placementUUID": "c0f17482-ca65-4e58-8446-d1b155f45945"}),
    responses=REQUEST_CONSTRUCTIVES_RESPONSES,
)
async def request_constructives(
    request: Request,
    _: PhpBasicAuthDep,
    service: RequestConstructivesServiceDep,
) -> Response:
    return await service.request_constructives(php_json_decode(await request.body()))
