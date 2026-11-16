from pydantic import Field

from src.schema.base import BaseSchema

# Every field is loose on purpose: the rules live in crm.request_from_bi_badge, and its refusal names
# the field to mark. A strict type here would answer FastAPI's own 422 that the page cannot place.
type FormValue = str | int | float | None


class BiBadgeLeadRequest(BaseSchema):
    name: FormValue = None
    phone: FormValue = None
    resident_id: FormValue = None
    resident_other: FormValue = None
    area: FormValue = None
    ref: FormValue = None
    utm_source: FormValue = None
    utm_medium: FormValue = None
    utm_campaign: FormValue = None
    utm_content: FormValue = None
    utm_term: FormValue = None
    lang: FormValue = None
    device_id: FormValue = None
    website: FormValue = Field(
        default=None,
        description="Ловушка для ботов: поле скрыто от людей. Заполнено — ответ как об успехе, "
        "заявка не создаётся.",
    )


class BiBadgeLeadResponse(BaseSchema):
    ok: bool = True


class BiBadgeErrorResponse(BaseSchema):
    field: str | None = Field(
        default=None,
        description="Поле формы, под которым показать ошибку; null — ошибка всей формы",
    )
    message: str


class BiBadgeResident(BaseSchema):
    resident_id: int
    resident_name: str
    city_id: int | None = None
    city_name: str | None = None
