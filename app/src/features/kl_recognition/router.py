from typing import Annotated

from fastapi import APIRouter, Body

from src.routers.config import api_prefix_config

from .auth import KlRecognitionBasicAuthDep
from .deps import KlRecognitionServiceDep
from .openapi_examples import (
    RECOGNIZE_FREE_LAYOUT,
    RECOGNIZE_NO_KL,
    RECOGNIZE_RECOGNIZED,
    RECOGNIZE_REQUEST,
    RECOGNIZE_SUM_MISMATCH,
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
        "площади по цепочке `room_tab`), считаются сумма и статус: `recognized` — сумма "
        "`financial_area` сошлась с общей площадью листа и с `bi_area` (±0,03) и все комнаты "
        "определены, заполнять автоматически; иначе `not_recognized` без комнат, причина в "
        "`reason` — планировку заводят вручную. Нет КЛ, нет плана, агент не ответил — тоже "
        "`not_recognized` (HTTP 200).\n\n"
        "Ответ за 15–30 с; без БД. Вызывает фоновая обработка планировок."
    ),
    responses={
        200: {
            "description": "Результат распознавания",
            "content": {
                "application/json": {
                    "examples": {
                        "recognized": {"summary": "Распознано", "value": RECOGNIZE_RECOGNIZED},
                        "sum_mismatch": {
                            "summary": "Не распознано: сумма не сошлась",
                            "value": RECOGNIZE_SUM_MISMATCH,
                        },
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
