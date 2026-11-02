"""PHP ``IntegrationController::showroomInfoListAction``. Any HTTP method and Zend address form.

The caller check of the router runs first.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Response

from src.php_compat.zend_request import php_route

from .auth import require_showroom_bi_config, require_showroom_cron_auth
from .deps import ShowroomServiceDep
from .openapi_examples import SHOWROOM_INFO_LIST_RESPONSES

router = APIRouter(tags=["Showroom (BI Group)"], dependencies=[require_showroom_cron_auth])


@php_route(
    router,
    "/showroom-info-list",
    method="GET",
    summary="Отправить список шоурумов в BI Group",
    description=(
        "Вызывает крон раз в день. Функция БД: `rest.read_marketing_remont_list`, её результат "
        "уходит POST на `SHOWROOM_BI_URL`. Ответ BI Group отдаётся как есть, код всегда 200."
    ),
    responses=SHOWROOM_INFO_LIST_RESPONSES,
)
async def showroom_info_list(
    _: Annotated[None, Depends(require_showroom_bi_config)],
    service: ShowroomServiceDep,
) -> Response:
    return await service.send_list_to_bi_group()
