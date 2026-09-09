from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, Request, status
from fastapi.responses import Response
from src.integration_context.constants import SCOPE_FACTORING
from src.integration_context.deps import IntegrationContextDep
from src.integration_context.helpers import employee_id_from_context
from src.routers.config import api_prefix_config

from .auth import FactoringBasicAuthDep
from .deps import FactoringServiceDep
from .openapi_examples import (
    APPLICATION_RESPONSES,
    CREATE_APPLICATION_RESPONSES,
    WEBHOOK_ACK_RESPONSES,
)
from .schemas import (
    CreateFactoringApplicationResponse,
    CreateFactoringRefundRequest,
    FactoringApplicationListResponse,
    FactoringApplicationResponse,
    FactoringProviderConfigResponse,
    FactoringRefundResponse,
    FactoringRefundWebhookPayload,
    FactoringWebhookPayload,
    PrepareFactoringDocumentsRequest,
    PrepareFactoringDocumentsResponse,
    PrescoringFactoringRequest,
    PrescoringFactoringResponse,
    SendCessionRequest,
    SendCessionResponse,
    SubmitFactoringApplicationRequest,
    WebhookAckResponse,
)

router = APIRouter(prefix=api_prefix_config.v1.factoring_ff, tags=["Factoring (Freedom Finance)"])


@router.get(
    "/config",
    response_model=FactoringProviderConfigResponse,
    summary="Сроки и тарифы факторинга из integration_provider_tab.config",
)
async def get_provider_config(
    _: FactoringBasicAuthDep,
    context: IntegrationContextDep,
    service: FactoringServiceDep,
) -> FactoringProviderConfigResponse:
    if context is not None:
        context.require_scope(SCOPE_FACTORING)
    return await service.get_provider_config()


@router.get(
    "/applications",
    response_model=FactoringApplicationListResponse,
    summary="Список факторинговых заявок по client_request_id",
)
async def list_applications(
    _: FactoringBasicAuthDep,
    context: IntegrationContextDep,
    client_request_id: int,
    service: FactoringServiceDep,
) -> FactoringApplicationListResponse:
    if context is not None:
        context.require_client_request(SCOPE_FACTORING, client_request_id)
    items = await service.get_applications_by_client_request(client_request_id)
    return FactoringApplicationListResponse(items=items, total=len(items))


@router.post(
    "/applications",
    status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
    summary="Отключён: one-shot create минует ЭЦП",
    description=(
        "Прямой apply без prepare/submit больше не принимается. "
        "Используйте `POST /applications/prepare`, затем `POST /applications/{id}/submit`."
    ),
)
async def create_application_disabled(_: FactoringBasicAuthDep) -> None:
    raise HTTPException(
        status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
        detail="Use POST /applications/prepare then POST /applications/{id}/submit.",
    )


@router.post(
    "/applications/prepare",
    response_model=PrepareFactoringDocumentsResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Подготовить печатные формы и отправить клиенту на ЭЦП",
)
async def prepare_documents(
    _: FactoringBasicAuthDep,
    context: IntegrationContextDep,
    request: PrepareFactoringDocumentsRequest,
    service: FactoringServiceDep,
) -> PrepareFactoringDocumentsResponse:
    if context is not None:
        context.require_client_request(SCOPE_FACTORING, request.client_request_id)
        request = request.model_copy(
            update={"created_by": employee_id_from_context(context, fallback=request.created_by)}
        )
    return await service.prepare_documents(request)


@router.post(
    "/applications/prescoring",
    response_model=PrescoringFactoringResponse,
    summary="Прескоринг клиента перед факторингом (Freedom ML)",
    description=(
        "Вызывает bank `prescoring_factoring` до prepare/submit. "
        "Требует `config.prescoring_base_url` и env `FACTORING_PRESCORING_USER/PASSWORD`. "
        "При `prescoring_required=false` недоступность сервиса не блокирует (dev)."
    ),
)
async def prescoring_application(
    _: FactoringBasicAuthDep,
    context: IntegrationContextDep,
    request: PrescoringFactoringRequest,
    service: FactoringServiceDep,
) -> PrescoringFactoringResponse:
    if context is not None:
        context.require_client_request(SCOPE_FACTORING, request.client_request_id)
    return await service.run_prescoring(request)


@router.post(
    "/applications/{application_id}/refresh-sign",
    response_model=PrepareFactoringDocumentsResponse,
    summary="Проверить статусы ЭЦП печатных форм",
)
async def refresh_sign_status(
    _: FactoringBasicAuthDep,
    context: IntegrationContextDep,
    application_id: Annotated[int, Path(examples=[1])],
    service: FactoringServiceDep,
) -> PrepareFactoringDocumentsResponse:
    if context is not None:
        context.require_application(SCOPE_FACTORING, application_id)
    return await service.refresh_sign_status(application_id)


@router.post(
    "/applications/{application_id}/submit",
    response_model=CreateFactoringApplicationResponse,
    summary="Отправить подписанную заявку в банк",
    responses=CREATE_APPLICATION_RESPONSES,
)
async def submit_application(
    _: FactoringBasicAuthDep,
    context: IntegrationContextDep,
    application_id: Annotated[int, Path(examples=[1])],
    request: SubmitFactoringApplicationRequest,
    service: FactoringServiceDep,
) -> CreateFactoringApplicationResponse:
    if context is not None:
        context.require_application(SCOPE_FACTORING, application_id)
    return await service.submit_application(application_id, request)


@router.get(
    "/print-forms/{application_id}/{name}",
    summary="Публичный PDF печатной формы (для банка после ЭЦП)",
)
async def download_print_form(
    application_id: int,
    name: str,
    service: FactoringServiceDep,
    t: Annotated[str, Query(description="file_token")],
) -> Response:
    pdf = await service.get_print_form_file(application_id, name, t)
    return Response(content=pdf, media_type="application/pdf")


@router.post(
    "/sign-callback",
    response_model=WebhookAckResponse,
    summary="Callback MyNCA после подписи (no-op, статус проверяем poll)",
)
async def sign_callback() -> WebhookAckResponse:
    return WebhookAckResponse(ok=True, status=True)


@router.post(
    "/applications/cession/send",
    response_model=SendCessionResponse,
    summary="Собрать выдачи за день и отправить банку договор цессии",
    description=(
        "Собирает FACTORING-заявки со статусом ISSUED/REVERSED за `issue_date` (по умолчанию "
        "сегодня, Asia/Almaty), которые ещё не были в цессии, группирует по ТОО "
        "(`client_request_tab.company_id`) и отправляет **отдельный** договор цессии на "
        "каждое юрлицо (у каждого своя `config.partner_by_company_id` и свой ЭЦП в "
        "`nca.company_key_store_tab`). Подписывает CMS через MyNCA. При успехе помечает "
        "заявки `cession_sent_at`/`cession_contract_number`. Батчи без `partner` в мапе "
        "возвращаются с `sent=false`. Пятница–воскресенье банк ждёт три отдельных запроса "
        "в понедельник (по одному на каждый issue_date) — вызывающая сторона (cron) должна "
        "вызвать этот эндпоинт трижды с разными `issue_date`."
    ),
)
async def send_cession(
    _: FactoringBasicAuthDep,
    request: SendCessionRequest,
    service: FactoringServiceDep,
) -> SendCessionResponse:
    return await service.send_daily_cession(request)


@router.get(
    "/applications/{application_id}",
    response_model=FactoringApplicationResponse,
    summary="Статус факторинговой заявки",
    responses=APPLICATION_RESPONSES,
)
async def get_application(
    _: FactoringBasicAuthDep,
    context: IntegrationContextDep,
    application_id: Annotated[
        int,
        Path(description="ID в installment_application_tab (product_type = FACTORING)", examples=[1]),
    ],
    service: FactoringServiceDep,
) -> FactoringApplicationResponse:
    if context is not None:
        context.require_application(SCOPE_FACTORING, application_id)
    return await service.get_application_by_id(application_id)


@router.post(
    "/applications/{application_id}/refund",
    response_model=FactoringRefundResponse,
    summary="Вызвать возврат (FULL/PARTIAL) в Freedom factoring-refund",
)
async def create_refund(
    _: FactoringBasicAuthDep,
    context: IntegrationContextDep,
    application_id: Annotated[int, Path(examples=[29])],
    request: CreateFactoringRefundRequest,
    service: FactoringServiceDep,
) -> FactoringRefundResponse:
    if context is not None:
        context.require_application(SCOPE_FACTORING, application_id)
    return await service.create_refund(application_id, request)


@router.post(
    "/webhook",
    response_model=WebhookAckResponse,
    summary="Webhook статусов факторинга от Freedom",
    description=(
        "Входящий hook. Basic Auth обязателен (`webhook_username`/`webhook_password` у `FF_FACTORING`). "
        "Без кредов в БД hook отклоняется, заявку подать нельзя."
    ),
    responses=WEBHOOK_ACK_RESPONSES,
)
async def webhook_factoring(
    request: Request,
    body: FactoringWebhookPayload,
    service: FactoringServiceDep,
) -> WebhookAckResponse:
    payload = body.model_dump()
    if body.__pydantic_extra__:
        payload.update(body.__pydantic_extra__)
    authorization_header = request.headers.get("Authorization")
    return await service.handle_webhook(payload, authorization_header=authorization_header)


@router.post(
    "/webhook/refund",
    response_model=WebhookAckResponse,
    summary="Callback Colvir по возврату (covlir_status)",
)
async def webhook_factoring_refund(
    request: Request,
    body: FactoringRefundWebhookPayload,
    service: FactoringServiceDep,
) -> WebhookAckResponse:
    payload = body.model_dump()
    if body.__pydantic_extra__:
        payload.update(body.__pydantic_extra__)
    authorization_header = request.headers.get("Authorization")
    return await service.handle_refund_webhook(
        payload, authorization_header=authorization_header
    )
