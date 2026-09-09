from typing import Annotated

from fastapi import APIRouter, Path, Request, status
from src.integration_context.constants import SCOPE_INSTALLMENT
from src.integration_context.deps import IntegrationContextDep
from src.integration_context.helpers import employee_id_from_context
from src.routers import api_prefix_config

from .auth import InstallmentBasicAuthDep
from .deps import FFServiceDep
from .openapi_examples import (
    APPLICATION_RESPONSES,
    CREATE_APPLICATION_RESPONSES,
    FF_PRODUCTS_RESPONSES,
    PROVIDER_PRODUCTS_RESPONSES,
    SYNC_PRODUCTS_RESPONSES,
    WEBHOOK_ACK_RESPONSES,
)
from .schemas import (
    AllowedBankListResponse,
    ApplyInstallmentApplicationRequest,
    ApplyInstallmentApplicationResponse,
    CreateInstallmentApplicationRequest,
    CreateInstallmentApplicationResponse,
    FFWebhookPayload,
    FFProductsResponse,
    InstallmentApplicationListResponse,
    InstallmentApplicationResponse,
    ProviderProductListResponse,
    SyncProductsResponse,
    WebhookAckResponse,
)

router = APIRouter(prefix=api_prefix_config.v1.installment_ff, tags=["Installment (Freedom Finance)"])


@router.get(
    "/products",
    response_model=FFProductsResponse,
    summary="Условия продуктов FF",
    description=(
        "Прокси к Freedom Finance `get-partner-info`. "
        "Возвращает доступные `product_id`, диапазоны сумм и сроки для UI."
    ),
    responses=FF_PRODUCTS_RESPONSES,
)
async def get_ff_products(
    _: InstallmentBasicAuthDep,
    context: IntegrationContextDep,
    ff_service: FFServiceDep,
) -> FFProductsResponse:
    if context is not None:
        context.require_scope(SCOPE_INSTALLMENT)
    return await ff_service.get_products()


@router.post(
    "/sync-products",
    response_model=SyncProductsResponse,
    summary="Sync FF products into provider catalog",
    description=(
        "Загружает каталог из FF `get-partner-info` и append-only sync в "
        "`installment_provider_product_tab` (explode по period + principal limits). "
        "Запускать вручную или по cron раз в день."
    ),
    responses=SYNC_PRODUCTS_RESPONSES,
)
async def sync_products(
    _: InstallmentBasicAuthDep,
    ff_service: FFServiceDep,
) -> SyncProductsResponse:
    return await ff_service.sync_products()


@router.get(
    "/provider-products",
    response_model=ProviderProductListResponse,
    summary="Каталог продуктов провайдера",
    description=(
        "Список строк из `installment_provider_product_tab` после sync. "
        "`current_only=true` — только актуальные версии (`valid_to IS NULL`)."
    ),
    responses=PROVIDER_PRODUCTS_RESPONSES,
)
async def list_provider_products(
    _: InstallmentBasicAuthDep,
    ff_service: FFServiceDep,
    provider_code: str = "FF",
    current_only: bool = True,
) -> ProviderProductListResponse:
    return await ff_service.list_provider_products(provider_code, current_only=current_only)


@router.get(
    "/allowed-banks",
    response_model=AllowedBankListResponse,
    summary="Online-банки, доступные для сделки",
    description=(
        "Список online bank_tab через credit_program для client_request_id. "
        "Используется CRM при подаче заявки."
    ),
)
async def list_allowed_banks(
    _: InstallmentBasicAuthDep,
    context: IntegrationContextDep,
    client_request_id: int,
    ff_service: FFServiceDep,
) -> AllowedBankListResponse:
    if context is not None:
        context.require_client_request(SCOPE_INSTALLMENT, client_request_id)
    return await ff_service.get_allowed_banks_for_client_request(client_request_id)


@router.get(
    "/applications",
    response_model=InstallmentApplicationListResponse,
    summary="Список заявок по client_request_id",
)
async def list_applications(
    _: InstallmentBasicAuthDep,
    context: IntegrationContextDep,
    client_request_id: int,
    ff_service: FFServiceDep,
) -> InstallmentApplicationListResponse:
    if context is not None:
        context.require_client_request(SCOPE_INSTALLMENT, client_request_id)
    items = await ff_service.get_applications_by_client_request(client_request_id)
    return InstallmentApplicationListResponse(items=items, total=len(items))


@router.post(
    "/applications",
    response_model=CreateInstallmentApplicationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Создать заявку на рассрочку (FF)",
    description=(
        "Создаёт запись в БД, вызывает FF `goods-loan-lead`. "
        "**ИИН и телефон** передаются в теле — заёмщик может отличаться от клиента в `client_request_id`."
    ),
    responses=CREATE_APPLICATION_RESPONSES,
)
async def create_application(
    _: InstallmentBasicAuthDep,
    context: IntegrationContextDep,
    request: CreateInstallmentApplicationRequest,
    ff_service: FFServiceDep,
) -> CreateInstallmentApplicationResponse:
    if context is not None:
        context.require_client_request(SCOPE_INSTALLMENT, request.client_request_id)
        request = request.model_copy(
            update={"created_by": employee_id_from_context(context, fallback=request.created_by)}
        )
    return await ff_service.create_application(request)


@router.get(
    "/applications/{application_id}",
    response_model=InstallmentApplicationResponse,
    summary="Статус заявки",
    description="Локальное состояние из `installment_application_tab` (после webhook/poll).",
    responses=APPLICATION_RESPONSES,
)
async def get_application(
    _: InstallmentBasicAuthDep,
    context: IntegrationContextDep,
    application_id: Annotated[
        int,
        Path(description="ID в installment_application_tab", examples=[1]),
    ],
    ff_service: FFServiceDep,
) -> InstallmentApplicationResponse:
    if context is not None:
        context.require_application(SCOPE_INSTALLMENT, application_id)
    return await ff_service.get_application_by_id(application_id)


@router.post(
    "/applications/{application_id}/apply",
    response_model=ApplyInstallmentApplicationResponse,
    summary="Применить выданную заявку к сделке",
    description=(
        "Создаёт запись в `client_request_credit_detail_tab` по заявке в статусе ISSUED "
        "и связывает её через `installment_application_tab.client_request_credit_detail_id`. "
        "Повторный вызов идемпотентен — возвращает уже созданный credit_detail_id."
    ),
)
async def apply_application(
    _: InstallmentBasicAuthDep,
    context: IntegrationContextDep,
    application_id: Annotated[
        int,
        Path(description="ID в installment_application_tab", examples=[1]),
    ],
    request: ApplyInstallmentApplicationRequest,
    ff_service: FFServiceDep,
) -> ApplyInstallmentApplicationResponse:
    if context is not None:
        context.require_application(SCOPE_INSTALLMENT, application_id)
        created_by = employee_id_from_context(context, fallback=request.created_by)
    else:
        created_by = request.created_by
    return await ff_service.apply_application_to_deal(
        application_id,
        created_by=created_by,
    )


@router.post(
    "/applications/{application_id}/poll",
    response_model=InstallmentApplicationResponse,
    summary="Опрос статуса в FF",
    description="Вызывает FF `goods-application-info/{uuid}` и обновляет заявку в БД.",
    responses=APPLICATION_RESPONSES,
)
async def poll_application(
    _: InstallmentBasicAuthDep,
    context: IntegrationContextDep,
    application_id: Annotated[
        int,
        Path(description="ID в installment_application_tab", examples=[1]),
    ],
    ff_service: FFServiceDep,
) -> InstallmentApplicationResponse:
    if context is not None:
        context.require_application(SCOPE_INSTALLMENT, application_id)
    return await ff_service.poll_application(application_id)


@router.post(
    "/webhook",
    response_model=WebhookAckResponse,
    summary="Webhook от Freedom Finance",
    description=(
        "Входящий hook от банка. Basic Auth обязателен (`webhook_username` / `webhook_password` у провайдера FF). "
        "Без кредов в БД hook отклоняется, заявку подать нельзя."
        "В Swagger выберите пример: approved / rejected / issued."
    ),
    responses=WEBHOOK_ACK_RESPONSES,
)
async def webhook_ff(
    request: Request,
    body: FFWebhookPayload,
    ff_service: FFServiceDep,
) -> WebhookAckResponse:
    payload = body.model_dump()
    if body.__pydantic_extra__:
        payload.update(body.__pydantic_extra__)
    authorization_header = request.headers.get("Authorization")
    return await ff_service.handle_webhook(payload, authorization_header=authorization_header)
