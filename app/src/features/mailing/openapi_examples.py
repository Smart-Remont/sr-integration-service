"""Scalar examples."""

_COMMON: dict[int | str, dict[str, object]] = {
    401: {"description": "Нет учётки Basic или она неверная"},
}

FILL_SEND_EMAIL_RESPONSES: dict[int | str, dict[str, object]] = {
    200: {
        "description": "Пусто. При ошибке функции: её текст, код остаётся 200, как в PHP",
        "content": {"application/json; text/html; charset=utf-8": {}},
    },
    **_COMMON,
    503: {"description": "Не заданы `MAILING_CRON_*`"},
}
SEND_QUEUE_RESPONSES: dict[int | str, dict[str, object]] = {
    200: {
        "description": (
            "Пусто. Для письма, которое сервер не принял: текст отказа. Код остаётся 200, "
            "отказ записан в строке очереди"
        ),
        "content": {"application/json; text/html; charset=utf-8": {}},
    },
    **_COMMON,
    503: {
        "description": (
            "Нет настройки, либо не удалось подключиться к почтовому серверу, начать TLS или войти: "
            "тело называет причину, строки очереди не тронуты, повторит следующий запуск"
        ),
    },
}
PAID_REQUESTS_RESPONSES: dict[int | str, dict[str, object]] = {
    200: {
        "description": "Результат функции. Ошибка функции тоже идёт кодом 200, `status: false`",
        "content": {
            "application/json": {
                "examples": {
                    "ok": {"value": {"value": 0, "status": True, "notice": []}},
                    "failed": {
                        "value": {
                            "value": None,
                            "notice": [],
                            "error": "Текст ошибки",
                            "error_debug": "SQLSTATE[P0001]: ...",
                            "status": False,
                        }
                    },
                }
            }
        },
    },
    **_COMMON,
    503: {"description": "Не заданы `MAILING_CRON_*`"},
}
