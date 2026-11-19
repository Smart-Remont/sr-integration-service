from typing import Annotated

from fastapi import APIRouter, Path, Query, Request, status

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
    CreateInstallmentRefundRequest,
    FFProductsResponse,
    FFWebhookPayload,
    InstallmentApplicationListResponse,
    InstallmentApplicationResponse,
    InstallmentRefundResponse,
    InstallmentRefundWebhookPayload,
    ProviderProductListResponse,
    ReconcileResponse,
    SyncProductsResponse,
    WebhookAckResponse,
)

router = APIRouter(
    prefix=api_prefix_config.v1.installment_ff, tags=["Installment (Freedom Finance)"]
)


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
    "/applications/{application_id}/refund",
    response_model=InstallmentRefundResponse,
    summary="Вернуть займ в банк (расторжение)",
    description=(
        "Отправляет в Freedom `goods-loan-refund` по выданной рассрочке. REFUND — весь остаток займа, "
        "PARTIAL_REFUND — часть (`refund_amount` меньше остатка). Банк отвечает сразу, итог (Colvir) "
        "приходит хуком на `/webhook/refund`; полный успешный возврат закрывает заявку (REVERSED). "
        "504 — банк не ответил: запрос мог дойти, итог подтянет хук или `/reconcile`."
    ),
)
async def create_refund(
    _: InstallmentBasicAuthDep,
    context: IntegrationContextDep,
    application_id: Annotated[
        int,
        Path(description="ID в installment_application_tab", examples=[1]),
    ],
    request: CreateInstallmentRefundRequest,
    ff_service: FFServiceDep,
) -> InstallmentRefundResponse:
    if context is not None:
        context.require_application(SCOPE_INSTALLMENT, application_id)
        request = request.model_copy(
            update={
                "requested_by": employee_id_from_context(context, fallback=request.requested_by)
            }
        )
    return await ff_service.create_refund(application_id, request)


@router.post(
    "/webhook/refund",
    response_model=WebhookAckResponse,
    summary="Webhook Freedom: итог возврата",
    description=(
        "Банк присылает `{uuid, partner_id, refund_type, refund_amount, covlir_status}` после Colvir. "
        "Basic Auth — как у основного хука. Повтор и хук без запроса возврата принимаются (200) и "
        "пишутся в журнал."
    ),
)
async def webhook_refund(
    request: Request,
    body: InstallmentRefundWebhookPayload,
    ff_service: FFServiceDep,
) -> WebhookAckResponse:
    payload = body.model_dump(mode="json")
    if body.__pydantic_extra__:
        payload.update(body.__pydantic_extra__)
    return await ff_service.handle_refund_webhook(
        payload, authorization_header=request.headers.get("Authorization")
    )


@router.get(
    "/reconcile",
    response_model=ReconcileResponse,
    summary="Сверка открытых заявок с банком (cron)",
    description=(
        "Для cron раз в 5–10 минут. Запрашивает у банка статус заявок, которые давно не менялись; "
        "закрывает как APPLY_FAILED заявки без ответа банка на подачу старше `unknown_after_minutes`; "
        "записывает в сделку выданные, но не записанные. GET и POST — одно и то же."
    ),
)
@router.post("/reconcile", response_model=ReconcileResponse, include_in_schema=False)
async def reconcile_applications(
    _: InstallmentBasicAuthDep,
    ff_service: FFServiceDep,
    stale_minutes: Annotated[int, Query(ge=1, le=1440)] = 10,
    unknown_after_minutes: Annotated[int, Query(ge=61, le=10080)] = 120,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> ReconcileResponse:
    return await ff_service.reconcile(
        stale_minutes=stale_minutes,
        unknown_after_minutes=unknown_after_minutes,
        limit=limit,
    )


@router.post(
    "/webhook",
    response_model=WebhookAckResponse,
    summary="Webhook от Freedom Finance",
    description=(
        "Входящий hook от банка. Basic Auth обязателен (`webhook_username` / `webhook_password` у провайдера FF). "
        "Без кредов в БД hook отклоняется, заявку подать нельзя. "
        "Статус, которого нет в описании банка (например, возврат), принимается без изменений заявки. "
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
