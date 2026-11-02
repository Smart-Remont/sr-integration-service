"""PHP ``ClientController::kaspiRequestPayAction``. Any HTTP method and Zend address form, as in PHP.

The dependency of the router runs first, before any parameter or body is read.
"""

from fastapi import APIRouter, Depends, Request, Response

from src.shared.zend_request import php_route

from .auth import require_kaspi_proxy_secret
from .deps import KaspiServiceDep
from .openapi_examples import KASPI_PAY_PARAMETERS, KASPI_PAY_RESPONSES
from .params import read_kaspi_params

router = APIRouter(tags=["Kaspi"], dependencies=[Depends(require_kaspi_proxy_secret)])


@php_route(
    router,
    "/kaspi-request-pay",
    method="GET",
    summary="Kaspi: проверка и оплата по номеру заявки",
    description=(
        "Вызывает Kaspi через nginx, `command` = `check` или `pay`, `account` — номер заявки. "
        "Функции БД: `client.kaspi_check`, `client.kaspi_request_get_info`, "
        "`client.kaspi_request_pay`, `public.after_payment_operations`, "
        "`client.insert_kaspi_pay_log`."
    ),
    openapi_extra=KASPI_PAY_PARAMETERS,
    responses=KASPI_PAY_RESPONSES,
)
async def kaspi_request_pay(request: Request, service: KaspiServiceDep) -> Response:
    return await service.serve(await read_kaspi_params(request))
