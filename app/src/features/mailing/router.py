"""PHP ``RestController``: the crons of letters. Any HTTP method and Zend address form, as in PHP.

The caller check of the router runs first.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Response

from src.php_compat.zend_request import php_route

from .auth import (
    require_error_queue_config,
    require_mailing_cron_auth,
    require_provider_files_config,
    require_queue_config,
)
from .deps import MailingFunctionsServiceDep, MailingQueueServiceDep, ProviderFilesServiceDep
from .openapi_examples import (
    FILL_SEND_EMAIL_RESPONSES,
    PAID_REQUESTS_RESPONSES,
    PROVIDER_REQUESTS_RESPONSES,
    SEND_QUEUE_RESPONSES,
)

router = APIRouter(tags=["Рассылки и кроны"], dependencies=[require_mailing_cron_auth])


@php_route(
    router,
    "/fill-send-email",
    method="GET",
    summary="Заполнить очередь писем",
    description="Вызывает крон раз в 8 минут. Функция БД: `public.fill_send_email`.",
    responses=FILL_SEND_EMAIL_RESPONSES,
)
async def fill_send_email(service: MailingFunctionsServiceDep) -> Response:
    return await service.fill_send_email()


@php_route(
    router,
    "/send-email-new",
    method="GET",
    summary="Отправить очередь писем",
    description=(
        "Вызывает крон раз в 9 минут. Функции БД: `public.read_send_email`, "
        "`read_send_email_attachment`, `email_check_employee_subscribe`, "
        "`email_get_unsubscribe_info`, `email_get_remont_token`, `upd_email_send`, "
        "`upd_email_send_failed`, `email_unsubscribe_hard`. Строка отмечается отправленной до "
        "отправки: письмо не уйдёт дважды."
    ),
    responses=SEND_QUEUE_RESPONSES,
)
async def send_email_new(
    _: Annotated[None, Depends(require_queue_config)],
    service: MailingQueueServiceDep,
) -> Response:
    return await service.send_queue()


@php_route(
    router,
    "/send-email-error-db",
    method="GET",
    summary="Отправить письма об ошибках БД",
    description=(
        "Вызывает крон раз в 10 минут. Функции БД: `public.read_send_error`, `upd_error_send`, "
        "`upd_error_send_failed`. Функция чтения сейчас ничего не отбирает."
    ),
    responses=SEND_QUEUE_RESPONSES,
)
async def send_email_error_db(
    _: Annotated[None, Depends(require_error_queue_config)],
    service: MailingQueueServiceDep,
) -> Response:
    return await service.send_error_queue()


@php_route(
    router,
    "/provider-send-email-requests",
    method="GET",
    summary="Подготовить таблицы заявок для поставщиков",
    description=(
        "Вызывает крон раз в день. Функции БД: `provider.read_provider_for_unload`, "
        "`read_provider_request_remont_unload`, `insert_provider_send_email`, "
        "`insert_provider_send_email_attachment`. Пишет PDF через wkhtmltopdf. Письма не "
        "отправляет: эту очередь никто не читает."
    ),
    responses=PROVIDER_REQUESTS_RESPONSES,
)
async def provider_send_email_requests(
    _: Annotated[None, Depends(require_provider_files_config)],
    service: ProviderFilesServiceDep,
) -> Response:
    return await service.create_requests()


@php_route(
    router,
    "/paid-client-request-to-remont",
    method="GET",
    summary="Создать ремонты по оплаченным заявкам",
    description="Вызывает крон раз в день. Функция БД: `public.change_paid_client_request_to_remont`.",
    responses=PAID_REQUESTS_RESPONSES,
)
async def paid_client_request_to_remont(service: MailingFunctionsServiceDep) -> Response:
    return await service.paid_client_request_to_remont()
