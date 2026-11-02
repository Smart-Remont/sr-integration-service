"""Short BI Group lookups. PHP did not check the HTTP method here; GET is the documented one."""

from fastapi import APIRouter, Request, Response

from ..openapi_examples import (
    DDU_REQUEST_INFO_RESPONSES,
    REQUEST_INFO_RESPONSES,
    query_parameters,
)
from ..php_contract import PhpBasicAuthDep, php_route, read_param
from .deps import DduLookupServiceDep

router = APIRouter()

_REQUEST_KEYS = ("deal_id", "application_id", "order_id")


@php_route(
    router,
    "/request-info",
    method="GET",
    summary="Заявка по номерам ДДУ",
    description="Вызывает BI Group. Функция БД: `rest.ddu_request_info`.",
    openapi_extra=query_parameters(*_REQUEST_KEYS),
    responses=REQUEST_INFO_RESPONSES,
)
async def request_info(
    request: Request,
    _: PhpBasicAuthDep,
    service: DduLookupServiceDep,
) -> Response:
    keys = [await read_param(request, name) for name in _REQUEST_KEYS]
    return await service.request_info(*keys)


@php_route(
    router,
    "/request-status-info",
    method="GET",
    summary="Заявка и её статус по номерам ДДУ",
    description="Вызывает BI Group. Функция БД: `rest.ddu_request_and_status_info`.",
    openapi_extra=query_parameters(*_REQUEST_KEYS),
    responses=REQUEST_INFO_RESPONSES,
)
async def request_status_info(
    request: Request,
    _: PhpBasicAuthDep,
    service: DduLookupServiceDep,
) -> Response:
    keys = [await read_param(request, name) for name in _REQUEST_KEYS]
    return await service.request_status_info(*keys)


@php_route(
    router,
    "/ddu-request-info",
    method="GET",
    summary="Заявка по номеру заявки Smart Remont",
    description="Вызывает BI Group. Функция БД: `rest.ddu_request_info_by_client_request`.",
    openapi_extra=query_parameters("client_request_id"),
    responses=DDU_REQUEST_INFO_RESPONSES,
)
async def ddu_request_info(
    request: Request,
    _: PhpBasicAuthDep,
    service: DduLookupServiceDep,
) -> Response:
    return await service.ddu_request_info(await read_param(request, "client_request_id"))


@php_route(
    router,
    "/ddu-flat-info",
    method="GET",
    summary="Квартира по GUID",
    description="Вызывает BI Group. Функция БД: `rest.ddu_flat_info`.",
    openapi_extra=query_parameters("flat_guid"),
    responses=DDU_REQUEST_INFO_RESPONSES,
)
async def ddu_flat_info(
    request: Request,
    _: PhpBasicAuthDep,
    service: DduLookupServiceDep,
) -> Response:
    return await service.ddu_flat_info(await read_param(request, "flat_guid"))
