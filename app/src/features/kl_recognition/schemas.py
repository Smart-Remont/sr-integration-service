from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import Field, HttpUrl, model_validator

from src.schema import BaseSchema


class RecognitionStatus(StrEnum):
    RECOGNIZED = "recognized"
    NOT_RECOGNIZED = "not_recognized"
    FREE_LAYOUT = "free_layout"


class KlRecognizeRequest(BaseSchema):
    flat_guid: UUID | None = Field(
        default=None,
        description="GUID квартиры в BI (`flat_tab.flat_guid`): по нему скачивается КЛ.",
    )
    pdf_url: HttpUrl | None = Field(
        default=None, description="Прямая ссылка на PDF КЛ — вместо `flat_guid`."
    )
    bi_area: float | None = Field(
        default=None,
        description="Площадь квартиры от BI (`area_inner + area_balcony`) для второй сверки суммы.",
    )

    @model_validator(mode="after")
    def check_one_source(self) -> Self:
        if (self.flat_guid is None) == (self.pdf_url is None):
            raise ValueError("Нужен ровно один из flat_guid и pdf_url")
        return self


class KlRoomPart(BaseSchema):
    name: str
    area: float


class KlRoomRef(BaseSchema):
    name: str
    area: float
    room_id: int | None = Field(
        default=None, description="Комната системы соседа; null — терраса или не определена."
    )


class KlRoomNeighbors(BaseSchema):
    """Стороны — как на плане листа, а не стороны света."""

    left: list[KlRoomRef] = Field(default_factory=list)
    right: list[KlRoomRef] = Field(default_factory=list)
    up: list[KlRoomRef] = Field(default_factory=list)
    down: list[KlRoomRef] = Field(default_factory=list)


class KlRoom(BaseSchema):
    name: str = Field(description="Название, как напечатано на листе (`С/у`, `Гард.`, …).")
    area: float | None = Field(
        description="Площадь, м²; у лоджий и балконов — полная. null — на листе у помещения нет площади."
    )
    financial_area: float | None = Field(
        description="Площадь в общей сумме: у лоджий, балконов, террас — с коэффициентом, у остальных = area."
    )
    area_text: str | None = Field(
        default=None,
        description="Площадь дословно, как напечатана на листе (`2,51 (1,26)`); null — не напечатана.",
    )
    room_type: str = Field(
        description="Тип помещения по ответу агента: `Спальня`, `Холл`, `Кладовая`, … или `Другое`."
    )
    room_id: int | None = Field(
        description="Комната системы (`room_tab.room_id`) с номером по площади; null — не определена."
    )
    system_name: str | None = Field(
        description="Название комнаты системы: `Спальня №2`, `Коридор`, …; null — не определена."
    )
    merged_from: list[KlRoomPart] = Field(
        default_factory=list,
        description="Из чего сложена площадь: кухня и кладовая за пунктиром.",
    )
    neighbors: KlRoomNeighbors = Field(
        default_factory=KlRoomNeighbors,
        description=(
            "Смежные помещения по сторонам плана, по ответу модели. Внутри стороны — порядок вдоль "
            "стены: `left` и `right` сверху вниз, `up` и `down` слева направо."
        ),
    )


class KlProblemCode(StrEnum):
    AREA_NOT_PRINTED = "area_not_printed"
    AREA_NOT_IN_QUOTE = "area_not_in_quote"
    AREA_WITHOUT_QUOTE = "area_without_quote"


class KlProblem(BaseSchema):
    """Почему лист нельзя заполнять автоматически, по комнате. Сайт показывает это красным."""

    code: KlProblemCode = Field(
        description=(
            "`area_not_printed` — у помещения на листе нет площади; `area_not_in_quote` — число в ответе "
            "не совпадает с напечатанным; `area_without_quote` — модель не показала, где на листе это число."
        )
    )
    room: str | None = Field(default=None, description="Название помещения, как на листе.")
    area: float | None = Field(
        default=None,
        description="Площадь помещения, как её прочитала модель; null — не напечатана. По ней сайт понимает, "
        "какую из одноимённых комнат поправил человек.",
    )
    text: str = Field(description="Причина для человека.")


class KlChecks(BaseSchema):
    rooms_sum: float | None = Field(default=None, description="Сумма financial_area помещений.")
    sheet_total: float | None = Field(default=None, description="Общая площадь на листе.")
    sheet_ok: bool | None = Field(default=None, description="Сумма сошлась с листом (±0,03).")
    bi_area: float | None = None
    bi_ok: bool | None = Field(default=None, description="Сумма сошлась с площадью BI (±0,03).")


class KlRecognitionMeta(BaseSchema):
    model: str | None = None
    seconds: int | None = Field(default=None, description="Время работы агента.")
    input_tokens: int | None = None
    output_tokens: int | None = None


class KlRecognitionResult(BaseSchema):
    status: RecognitionStatus = Field(
        description=(
            "`recognized` — сумма комнат сошлась с листом и с BI, все комнаты определены, "
            "заполнять автоматически; `not_recognized` — не распознано, причина в `reason`, "
            "в `rooms` то, что удалось прочитать: оператор сверяет с листом; `free_layout` — свободная планировка, не "
            "распознаётся."
        )
    )
    reason: str = Field(description="Почему такой статус.")
    is_kitchen_studio: bool = Field(
        default=False,
        description=(
            "Кухня без стены к гостиной: кухня-ниша или граница пунктиром. Площади кухни и "
            "гостиной остаются раздельными, признак — для `planirovka_tab.is_kitchen_studio`."
        ),
    )
    rooms: list[KlRoom]
    terraces: list[KlRoomPart] = Field(
        default_factory=list,
        description="Террасы с листа: в сумме учтены, но комнатой системы не являются (площадь террасы хранится у квартиры).",
    )
    total_area: float | None = Field(description="Общая площадь на листе (или сумма комнат).")
    total_rooms: int | None = Field(description="Комнатность из кружка у общей площади.")
    checks: KlChecks
    doubts: list[str] = Field(description="Что агент счёл сомнительным.")
    problems: list[KlProblem] = Field(
        default_factory=list,
        description="Ошибки чтения, из-за которых лист не распознан: число не с листа, площади нет.",
    )
    meta: KlRecognitionMeta
