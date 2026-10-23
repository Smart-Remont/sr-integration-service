from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from ..auth import BigIntegrationBasicAuthDep
from .deps import NpsResultsServiceDep
from .openapi_examples import NPS_RESULTS_RESPONSES
from .service import MAX_PERIOD_DAYS

router = APIRouter()


@router.get(
    "/nps-results",
    summary="Результаты NPS-опросов по квартирам BI Group",
    description=(
        "Опросы, пройденные за период (`sp: rest.bi_nps_results`): после ремонта "
        "(`survey_type = REPAIR`) и о проектировщике (`PROJECT`). BI забирает раз в день; "
        "без дат — вчерашний день по времени Алматы. Ничего не помечается отданным: повторный "
        "запрос за те же дни вернёт те же опросы. Поля `*_rating` — блоки из ТЗ BI, `answers` — "
        f"все ответы опроса. Период — не больше {MAX_PERIOD_DAYS} дней."
    ),
    responses=NPS_RESULTS_RESPONSES,
)
async def nps_results(
    _: BigIntegrationBasicAuthDep,
    service: NpsResultsServiceDep,
    date_from: Annotated[
        str | None, Query(description="Первый день периода, YYYY-MM-DD, включительно")
    ] = None,
    date_to: Annotated[
        str | None, Query(description="Последний день периода, YYYY-MM-DD, включительно")
    ] = None,
) -> JSONResponse:
    return await service.nps_results(date_from, date_to)
