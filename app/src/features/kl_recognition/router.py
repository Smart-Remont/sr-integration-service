from typing import Annotated

from fastapi import APIRouter, Body

from src.routers.config import api_prefix_config

from .auth import KlRecognitionBasicAuthDep
from .deps import KlRecognitionServiceDep
from .openapi_examples import (
    RECOGNIZE_AUTO,
    RECOGNIZE_FREE_LAYOUT,
    RECOGNIZE_NO_KL,
    RECOGNIZE_REQUEST,
)
from .schemas import KlRecognitionResult, KlRecognizeRequest

router = APIRouter(prefix=api_prefix_config.v1.kl_recognition, tags=["KL Recognition (BI Group)"])


@router.post(
    "/recognize",
    response_model=KlRecognitionResult,
    summary="Распознать помещения и площади квартирного листа",
    description=(
        "Скачивает КЛ квартиры с `loadSheet` BI (или по `pdf_url`), вырезает план со стр. 2. "
        "Composer (Cursor SDK) сначала решает, свободная ли планировка: свободная — "
        "`free_layout`, комнаты не читаются. Иначе выписывает помещения с площадями как "
        "напечатаны и тип каждого. Здесь кладовая за пунктиром прибавляется к "
        "кухне (`merged_from`), помещения получают комнату системы (`room_id`, номер по "
        "площади по цепочке `room_tab`), считаются сумма и статус: `auto` — сумма "
        "`financial_area` сошлась с общей площадью листа и с `bi_area` (±0,03) и все комнаты "
        "определены; иначе `manual` с причиной в `reason`. Нет КЛ, нет плана, агент не "
        "ответил — тоже `manual` (HTTP 200).\n\n"
        "Ответ за 15–30 с; без БД. Вызывает фоновая обработка планировок."
    ),
    responses={
        200: {
            "description": "Результат распознавания",
            "content": {
                "application/json": {
                    "examples": {
                        "auto": {"summary": "Сумма сошлась", "value": RECOGNIZE_AUTO},
                        "free_layout": {
                            "summary": "Свободная планировка — не распознаётся",
                            "value": RECOGNIZE_FREE_LAYOUT,
                        },
                        "no_kl": {"summary": "У квартиры нет КЛ", "value": RECOGNIZE_NO_KL},
                    }
                }
            },
        },
    },
)
async def recognize_kl(
    _: KlRecognitionBasicAuthDep,
    body: Annotated[KlRecognizeRequest, Body(openapi_examples=RECOGNIZE_REQUEST)],
    service: KlRecognitionServiceDep,
) -> KlRecognitionResult:
    return await service.recognize(body)
