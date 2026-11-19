"""Scalar examples. The texts are the real PHP answers."""

_CONTENT_TYPE = "application/json"


def _error(description: str, text: str) -> dict[str, object]:
    return {"description": description, "content": {_CONTENT_TYPE: {"example": {"error": text}}}}


ALBATO_META_LEAD_RESPONSES: dict[int | str, dict[str, object]] = {
    201: {
        "description": "Лид создан",
        "content": {_CONTENT_TYPE: {"example": {"status": "ok", "crm_deal_id": 123456}}},
    },
    400: _error("Тело не JSON или нет `name`, `phone`", "Поле name обязательно"),
    401: _error("Неверный токен", "Неверный токен"),
    405: _error("Метод не POST, токен при этом верный", "Метод не поддерживается"),
    500: _error("Отказ функции БД", "Текст ошибки из функции БД"),
    503: _error(
        "Настройка `ALBATO_META_TOKEN` в БД пуста. В PHP здесь 500",
        "Не задан токен интеграции",
    ),
}
ALBATO_META_LEAD_REQUEST: dict[str, object] = {
    "parameters": [
        {"name": "token", "in": "query", "required": True, "schema": {"type": "string"}}
    ],
    "requestBody": {
        "required": True,
        "content": {
            _CONTENT_TYPE: {
                "example": {
                    "name": "Иванов Иван",
                    "phone": "77000000000",
                    "email": "client@example.com",
                    "form_name": "Форма",
                    "ad_name": "Реклама",
                    "page_name": "Страница",
                    "leadgen_id": "1",
                    "created_time": "2026-07-15 10:00:00",
                },
            },
        },
    },
}
