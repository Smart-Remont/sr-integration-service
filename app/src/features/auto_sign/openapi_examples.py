"""Scalar examples: these crons answer with an empty body, as PHP did."""

_AUTH_RESPONSES: dict[int | str, dict[str, object]] = {
    401: {"description": "Нет учётки Basic или она неверная"},
    503: {
        "description": "Не заданы `AUTO_SIGN_*` для этого шага, адрес MyNCA не https, для подписи `APP_ENV` не prod, либо нет соединения с БД"
    },
}
CRON_RESPONSES: dict[int | str, dict[str, object]] = {
    200: {
        "description": (
            "Пустой ответ: запуск окончен, идёт предыдущий запуск или не удалось прочитать список. "
            "Сбой отдельного документа пишется в лог и не меняет ответ."
        ),
        "content": {"text/html": {"example": ""}},
    },
    **_AUTH_RESPONSES,
}
