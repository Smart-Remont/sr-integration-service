from fastapi import APIRouter, Request, Response

from src.shared.php_json import php_json_decode
from src.shared.zend_request import php_route

from ..openapi_examples import PLACEMENT_BODY, PLACEMENT_PRESET_BODY, REMONT_PRESET_RESPONSES
from ..php_contract import PhpBasicAuthDep, method_not_allowed
from .deps import RemontPresetServiceDep

router = APIRouter()


@php_route(
    router,
    "/remont-preset-list-v2",
    method="POST",
    other_methods=method_not_allowed,
    summary="Пакеты ремонта квартиры с наполнением и рендерами",
    description=(
        "Вызывает BI Group, самый нагруженный маршрут. Функции БД: `rest.ddu__preset_list_v2`, "
        "`rest.render_plan_room__read`, `rest.render_preset_feature*`, `rest.render_filling*`, "
        "`rest.render_set_photo__read`, `rest.render__read`."
    ),
    openapi_extra=PLACEMENT_BODY,
    responses=REMONT_PRESET_RESPONSES,
)
async def remont_preset_list_v2(
    request: Request,
    _: PhpBasicAuthDep,
    service: RemontPresetServiceDep,
) -> Response:
    return await service.remont_preset_list(_json_object(await request.body()))


@php_route(
    router,
    "/remont-preset",
    method="POST",
    other_methods=method_not_allowed,
    summary="Пакет ремонта с ценами наполнения",
    description=(
        "Вызывает BI Group. Функции БД: `rest.ddu__preset_list_v2`, `rest.render_filling*`."
    ),
    openapi_extra=PLACEMENT_PRESET_BODY,
    responses=REMONT_PRESET_RESPONSES,
)
async def remont_preset(
    request: Request,
    _: PhpBasicAuthDep,
    service: RemontPresetServiceDep,
) -> Response:
    return await service.remont_preset(_json_object(await request.body()))


def _json_object(raw_body: bytes) -> dict[str, object]:
    # Broken JSON reaches the "empty placementUUID" check, as in PHP.
    body = php_json_decode(raw_body)
    return body if isinstance(body, dict) else {}
