"""Scalar examples. Texts of refusals are those of ``crm.request_from_bi_badge``."""

_CONTENT_TYPE = "application/json"


def _error(description: str, field: str | None, message: str) -> dict[str, object]:
    return {
        "description": description,
        "content": {_CONTENT_TYPE: {"example": {"field": field, "message": message}}},
    }


BI_BADGE_LEAD_RESPONSES: dict[int | str, dict[str, object]] = {
    200: {
        "description": "Заявка принята. Так же отвечает заполненная ловушка для ботов и дубль",
        "content": {_CONTENT_TYPE: {"example": {"ok": True}}},
    },
    422: _error(
        "Отказ функции БД: `field` — поле формы, `message` — текст для клиента",
        "phone",
        "Укажите номер телефона Казахстана: +7 7XX XXX XX XX",
    ),
    429: _error(
        "Лимит: 3 заявки с устройства или 30 с одного IP за 24 часа",
        None,
        "Слишком много заявок. Попробуйте завтра",
    ),
    500: _error(
        "Прочая ошибка БД, подробности только в журнале сервиса",
        None,
        "Не удалось отправить заявку. Попробуйте ещё раз или напишите нам в WhatsApp",
    ),
    503: _error(
        "Не задан `BI_BADGE_IP_SECRET`",
        None,
        "Форма временно недоступна. Напишите нам в WhatsApp",
    ),
}

BI_BADGE_LEAD_REQUEST: dict[str, object] = {
    "requestBody": {
        "required": True,
        "content": {
            _CONTENT_TYPE: {
                "example": {
                    "name": "Айгерим",
                    "phone": "+7 (701) 234-56-78",
                    "resident_id": 1234,
                    "area": "65,5",
                    "ref": "BI0001",
                    "utm_source": "bi_badge",
                    "utm_medium": "qr",
                    "utm_campaign": "bi_staff",
                    "lang": "ru",
                    "device_id": "3f1c2a9e-5b7d-4c11-9a2e-8f6d0b4c7e21",
                    "website": "",
                },
            },
        },
    },
}

BI_BADGE_RESIDENTS_RESPONSES: dict[int | str, dict[str, object]] = {
    200: {
        "description": "ЖК BI Group, по городу и названию",
        "content": {
            _CONTENT_TYPE: {
                "example": [
                    {
                        "resident_id": 1234,
                        "resident_name": "Arnau",
                        "city_id": 1,
                        "city_name": "Астана",
                    }
                ]
            }
        },
    },
}
