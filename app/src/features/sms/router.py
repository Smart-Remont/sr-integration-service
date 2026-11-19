"""PHP ``IntegrationController``: the crons ``sms-notify`` and ``sms-notify-status``.

Any HTTP method and Zend address form, as in PHP. The caller check of the router runs first.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Response

from src.php_compat.zend_request import php_route

from .auth import require_kcell_send_config, require_kcell_status_config, require_sms_cron_auth
from .deps import SmsServiceDep
from .openapi_examples import SMS_NOTIFY_RESPONSES, SMS_NOTIFY_STATUS_RESPONSES

router = APIRouter(tags=["SMS (Kcell)"], dependencies=[require_sms_cron_auth])


@php_route(
    router,
    "/sms-notify",
    method="GET",
    summary="Отправить очередь SMS через Kcell",
    description=(
        "Вызывает крон раз в минуту. Функции БД: `notify.sms_notify__read`, "
        "`notify.sms_notify_result__set`. Пачки по 200 сообщений."
    ),
    responses=SMS_NOTIFY_RESPONSES,
)
async def sms_notify(
    _: Annotated[None, Depends(require_kcell_send_config)],
    service: SmsServiceDep,
) -> Response:
    return await service.send_queued_messages()


@php_route(
    router,
    "/sms-notify-status",
    method="GET",
    summary="Обновить статусы отправленных пачек SMS",
    description=(
        "Вызывает крон раз в 10 минут. Функции БД: `notify.sms_notify_batch__read`, "
        "`notify.sms_notify_status__set`."
    ),
    responses=SMS_NOTIFY_STATUS_RESPONSES,
)
async def sms_notify_status(
    _: Annotated[None, Depends(require_kcell_status_config)],
    service: SmsServiceDep,
) -> Response:
    return await service.check_batch_statuses()
