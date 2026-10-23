from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import Field, HttpUrl, model_validator

from src.schema import BaseSchema


class RecognitionStatus(StrEnum):
    AUTO = "auto"
    MANUAL = "manual"


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


class KlRoom(BaseSchema):
    name: str = Field(description="Название, как напечатано на листе (`С/у`, `Гард.`, …).")
    area: float = Field(description="Площадь, м²; у лоджий и балконов — полная.")
    financial_area: float = Field(
        description="Площадь в общей сумме: у лоджий, балконов, террас — с коэффициентом, у остальных = area."
    )


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
        description="`auto` — сумма комнат сошлась с листом и с BI, поля можно заполнять; `manual` — проверить вручную."
    )
    reason: str = Field(description="Почему такой статус.")
    rooms: list[KlRoom]
    total_area: float | None = Field(description="Общая площадь на листе (или сумма комнат).")
    total_rooms: int | None = Field(description="Комнатность из кружка у общей площади.")
    checks: KlChecks
    doubts: list[str] = Field(description="Что агент счёл сомнительным.")
    meta: KlRecognitionMeta
