"""PHP ``IntegrationController``: the crons of company signing.

Any HTTP method and Zend address form, as in PHP. The caller check of the router runs first.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Response

from src.php_compat.zend_request import php_route

from .auth import (
    require_auto_sign_cron_auth,
    require_signing_config,
    require_upload_config,
)
from .deps import DocumentUploadServiceDep, OperatorSigningServiceDep
from .openapi_examples import CRON_RESPONSES

router = APIRouter(tags=["Auto signing (MyNCA)"], dependencies=[require_auto_sign_cron_auth])


@php_route(
    router,
    "/cron-auto-sign-operator",
    method="GET",
    summary="Подписать ключом компании документы, подписанные клиентом",
    description=(
        "Вызывает крон раз в минуту. Функции БД: `public.sign_read_for_auto_sign_operator`, "
        "`nca.company_key_store__get_active_by_company`, `client.insert_sign_general`, "
        "`client.sign_tab__modify`, `client.sign_tab__id_by_process`. Ставит настоящую подпись. "
        "Нужен `APP_ENV=prod`, иначе 503."
    ),
    responses=CRON_RESPONSES,
)
async def cron_auto_sign_operator(
    _: Annotated[None, Depends(require_signing_config)],
    service: OperatorSigningServiceDep,
) -> Response:
    return await service.sign_waiting_documents()


@php_route(
    router,
    "/cron-auto-upload-sign-doc",
    method="GET",
    summary="Положить подписанный договор в документы заявки",
    description=(
        "Вызывает крон раз в минуту. Функции БД: `public.sign_read_for_doc`, "
        "`public.sign_group_id_get_by_client_request`, `sale.sign_document_upd`. Файл из MyNCA "
        "записывается в MinIO."
    ),
    responses=CRON_RESPONSES,
)
async def cron_auto_upload_sign_doc(
    _: Annotated[None, Depends(require_upload_config)],
    service: DocumentUploadServiceDep,
) -> Response:
    return await service.upload_signed_documents()
