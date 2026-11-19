"""PHP ``RestController::buhAction``. Any HTTP method and Zend address form, as in PHP."""

from fastapi import APIRouter, Depends, Request, Response

from src.php_compat.zend_request import php_param_text, php_raw_body, php_route, read_params

from .auth import require_buh_1c_login
from .deps import Buh1cServiceDep
from .openapi_examples import BUH_REQUEST, BUH_RESPONSES

router = APIRouter(tags=["1C exchange"], dependencies=[Depends(require_buh_1c_login)])


@php_route(
    router,
    "/buh",
    method="POST",
    summary="Обмен с 1С",
    description=(
        "Вызывает 1С. Сначала `rest.get_buh_url`, её ошибка даёт 400. По `what`: `lot` "
        "(`GET`, `rest.buh_lot_read`), `lot_guid`, `material_guid`, `warehouse_guid` "
        "(`rest.buh_lot_upd`, `rest.buh_material_upd`, `rest.buh_warehouse_upd`, тело JSON). "
        "Журнал `public.insert_1c_json`. Остальные `what` из PHP не перенесены и ничего не делают."
    ),
    openapi_extra=BUH_REQUEST,
    responses=BUH_RESPONSES,
)
async def exchange(request: Request, service: Buh1cServiceDep) -> Response:
    raw_body = await php_raw_body(request)
    params = await read_params(request)
    return await service.exchange(what=php_param_text(params.get("what")), raw_body=raw_body)
