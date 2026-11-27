"""Routes of the BI Finance flow and of the MySpace page «Рассрочки». Same prefix and auth as
``router.py``; create and read of applications stay there for both banks."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Body, Path, Query, Request, status
from fastapi.responses import JSONResponse

from src.integration_context.constants import SCOPE_INSTALLMENT
from src.integration_context.deps import IntegrationContextDep
from src.integration_context.helpers import employee_id_from_context
from src.routers import api_prefix_config

from .auth import InstallmentBasicAuthDep
from .bif.webhook import BIFApplicationNotFoundError
from .constants import ProviderCode
from .deps import BIFServiceDep, BIFWebhookServiceDep, InstallmentOAServiceDep
from .schemas import (
    BIFWebhookNotFound,
    BIFWebhookPayload,
    ExpireResponse,
    InstallmentApplicationResponse,
    InstallmentOAApplicationListResponse,
    InstallmentStatsResponse,
    ManualRefundRequest,
    WebhookAckResponse,
)

router = APIRouter(
    prefix=api_prefix_config.v1.installment_ff, tags=["Installment (Freedom Finance)"]
)


@router.post(
    "/webhook/bif",
    response_model=WebhookAckResponse,
    summary="Webhook BI Finance: статус заявки",
    description=(
        "BI Finance присылает `{appId, status, approvedConditions?, comment?}` (ТЗ, раздел 4). "
        "`installment__application_update_from_bif`: повтор того же статуса и статус после конечного "
        "ничего не меняют (200). ISSUED — кредит пишется в сделку (`cr_credit_detail__insert_from_installment`). "
        "Basic Auth — `webhook_username` / `webhook_password` провайдера BIF; не настроен — 503. "
        'Неизвестный appId — 404 `{"code": "APP_NOT_FOUND"}`, неверное тело — 400.'
    ),
    responses={
        status.HTTP_404_NOT_FOUND: {"model": BIFWebhookNotFound},
        status.HTTP_400_BAD_REQUEST: {"description": "Тело не по формату ТЗ"},
    },
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": BIFWebhookPayload.model_json_schema()}},
        }
    },
)
async def webhook_bif(
    request: Request, webhook_service: BIFWebhookServiceDep
) -> WebhookAckResponse | JSONResponse:
    # The raw body: the bank expects 400, not FastAPI's 422, for a malformed one.
    try:
        return await webhook_service.handle_status(
            await request.body(), authorization_header=request.headers.get("Authorization")
        )
    except BIFApplicationNotFoundError:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND, content=BIFWebhookNotFound().model_dump()
        )


@router.post(
    "/applications/{application_id}/refund/manual",
    response_model=InstallmentApplicationResponse,
    summary="Отметить возврат BI Finance",
    description=(
        "Только BI Finance: банк в возврате не участвует, сотрудник отмечает факт "
        "(`installment__application_refund_manual`). Только из ISSUED; сумма всех возвратов ≤ "
        "суммы займа; вся сумма — заявка REVERSED. Ставит `refund_mode=MANUAL`, "
        "`refund_status=SUCCESS`, `refund_completed_at` = `refunded_at` (по умолчанию сейчас). "
        "Отвечает заявкой после отметки. Вызывает MySpace."
    ),
)
async def mark_manual_refund(
    _: InstallmentBasicAuthDep,
    context: IntegrationContextDep,
    application_id: Annotated[int, Path(description="ID в installment_application_tab")],
    body: Annotated[ManualRefundRequest, Body()],
    bif_service: BIFServiceDep,
) -> InstallmentApplicationResponse:
    if context is not None:
        context.require_application(SCOPE_INSTALLMENT, application_id)
        body = body.model_copy(
            update={"created_by": employee_id_from_context(context, fallback=body.created_by)}
        )
    return await bif_service.mark_manual_refund(application_id, body)


@router.post(
    "/expire",
    response_model=ExpireResponse,
    summary="Закрыть заявки без ответа банка (EXPIRED)",
    description=(
        "Заявки с прошедшим `expires_at` (BI Finance: 3 рабочих дня после создания) без итога "
        "банка → EXPIRED (`installment__applications_expire`). Крон базы зовёт ту же функцию "
        "напрямую (`cron__installment_expire` в `cronrun__every_10_minutes`); здесь — ручной запуск."
    ),
)
async def expire_applications(
    _: InstallmentBasicAuthDep,
    bif_service: BIFServiceDep,
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
) -> ExpireResponse:
    return await bif_service.expire_applications(limit=limit)


@router.get(
    "/oa/applications",
    response_model=InstallmentOAApplicationListResponse,
    summary="Заявки обоих банков для MySpace",
    description=(
        "`installment__oa_applications_list`: заявка + ФИО клиента, город и ТОО сделки. Даты — по "
        "`created_at`, Алматы, включительно. `search` — номер заявки на ремонт или сделки CRM, ИИН, "
        "часть ФИО. Вызывает MySpace (scope `installment`)."
    ),
)
async def list_oa_applications(
    _: InstallmentBasicAuthDep,
    context: IntegrationContextDep,
    oa_service: InstallmentOAServiceDep,
    provider_code: ProviderCode | None = None,
    status_: Annotated[str | None, Query(alias="status", max_length=40)] = None,
    date_from: date | None = None,
    date_to: date | None = None,
    client_request_id: int | None = None,
    search: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> InstallmentOAApplicationListResponse:
    if context is not None:
        context.require_scope(SCOPE_INSTALLMENT)
    return await oa_service.list_applications(
        provider_code=provider_code,
        status=status_,
        date_from=date_from,
        date_to=date_to,
        client_request_id=client_request_id,
        search=search,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/oa/stats",
    response_model=InstallmentStatsResponse,
    summary="Аналитика заявок для MySpace",
    description=(
        "`installment__oa_stats`: по каждому банку и итог — число и сумма по статусам, выдано "
        "(ISSUED и потом возвращённые), конверсия выдано / создано, возвраты. Сумма — займа "
        "(одобренная банком, иначе запрошенная). Фильтры — как у `/oa/applications`."
    ),
)
async def get_oa_stats(
    _: InstallmentBasicAuthDep,
    context: IntegrationContextDep,
    oa_service: InstallmentOAServiceDep,
    provider_code: ProviderCode | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> InstallmentStatsResponse:
    if context is not None:
        context.require_scope(SCOPE_INSTALLMENT)
    return await oa_service.stats(provider_code=provider_code, date_from=date_from, date_to=date_to)
