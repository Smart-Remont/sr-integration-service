"""Scalar examples."""

SHOWROOM_INFO_LIST_RESPONSES: dict[int | str, dict[str, object]] = {
    200: {
        "description": (
            "Тело ответа BI Group как есть, при любом его коде. Пустое, если BI Group не ответил"
        ),
        "content": {
            "text/html": {
                "examples": {
                    "bi_group_answered": {
                        "value": '{"status":"success","message":"Список принят"}'
                    },
                    "bi_group_silent": {"value": ""},
                }
            }
        },
    },
    401: {"description": "Нет учётки Basic или она неверная"},
    503: {"description": "Не заданы `SHOWROOM_CRON_*` или `SHOWROOM_BI_URL`"},
}
