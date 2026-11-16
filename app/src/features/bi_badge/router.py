"""Form of BI badges: the page, the list of residential complexes and the application.

Public on purpose: a client opens the page from the QR code on a badge, and the page calls the API
of the same address. The defence is in the database (limits per device and per IP hash, duplicates
of a phone) and in the trap field. Technical debt, see CLAUDE.md.
"""

from fastapi import APIRouter, Response
from fastapi.responses import HTMLResponse

from .client_ip import ClientIpDep
from .deps import BiBadgeServiceDep
from .openapi_examples import (
    BI_BADGE_LEAD_REQUEST,
    BI_BADGE_LEAD_RESPONSES,
    BI_BADGE_RESIDENTS_RESPONSES,
)
from .page import bi_badge_page_response
from .schemas import BiBadgeLeadRequest, BiBadgeLeadResponse, BiBadgeResident
from .service import RESIDENTS_CACHE_SECONDS

TAG = "BI badges"
PAGE_PATH = "/bi"

router = APIRouter(tags=[TAG])
page_router = APIRouter(tags=[TAG])


@page_router.get(
    PAGE_PATH,
    response_class=HTMLResponse,
    summary="Страница формы",
    description=(
        "Открывает клиент по QR с бейджа: `/bi?ref=BI0001&utm_source=bi_badge&utm_medium=qr"
        "&utm_campaign=bi_staff`. Одна страница, стили и скрипт внутри, без БД. Языки RU и KZ."
    ),
)
async def bi_badge_page() -> HTMLResponse:
    return bi_badge_page_response()


@router.get(
    "/residents",
    response_model=list[BiBadgeResident],
    summary="ЖК BI Group для формы",
    description=(
        "Вызывает страница формы. Функция БД: `crm.bi_badge_resident_read(cur, null)`. "
        f"Без персональных данных. Кэш в памяти воркера на {RESIDENTS_CACHE_SECONDS} с."
    ),
    responses=BI_BADGE_RESIDENTS_RESPONSES,
)
async def bi_badge_residents(
    service: BiBadgeServiceDep, response: Response
) -> list[BiBadgeResident]:
    response.headers["Cache-Control"] = f"public, max-age={RESIDENTS_CACHE_SECONDS}"
    return await service.residents()


@router.post(
    "/lead",
    response_model=BiBadgeLeadResponse,
    summary="Заявка с формы",
    description=(
        "Вызывает страница формы. Функция БД: `crm.request_from_bi_badge(json_)`, она создаёт "
        "минимальный лид с источником «Бейджи BI». Заполненное поле-ловушка `website`: ответ "
        "как об успехе, функция БД не вызывается. IP клиента уходит в БД только как HMAC-SHA256 "
        "(`BI_BADGE_IP_SECRET`). Номер сделки и признак дубля наружу не отдаются."
    ),
    openapi_extra=BI_BADGE_LEAD_REQUEST,
    responses=BI_BADGE_LEAD_RESPONSES,
)
async def bi_badge_lead(
    lead: BiBadgeLeadRequest,
    client_ip: ClientIpDep,
    service: BiBadgeServiceDep,
) -> BiBadgeLeadResponse:
    await service.create_lead(lead, client_ip=client_ip)
    return BiBadgeLeadResponse()
