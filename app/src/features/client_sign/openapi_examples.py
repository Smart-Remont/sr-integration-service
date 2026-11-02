"""Scalar examples. Shapes are the real PHP responses, with made-up values."""

PAGE_RESPONSES = {
    200: {
        "description": "HTML как в PHP",
        "content": {"text/html": {"example": "<!doctype html>..."}},
    },
}
FILE_RESPONSES = {
    200: {
        "description": "Файл или страница с текстом `Ошибка: ...` с кодом 200, как в PHP",
        "content": {"application/pdf": {}},
    },
    302: {"description": "Только `doc_type=AGREEMENT_MANUAL`: переход на файл"},
    503: {"description": "Адрес партнёра или учётка MyNCA не настроены"},
}
PERFORM_SIGN_RESPONSES = {
    302: {"description": "Переход на страницу подписания MyNCA"},
    200: {
        "description": "Ошибка подписания: страница с текстом, код 200, как в PHP",
        "content": {"text/html": {}},
    },
    503: {"description": "Адрес MyNCA, адрес сервиса или секрет колбэка не настроены"},
}
INTEGRATION_FILE_RESPONSES = {
    200: {"description": "PDF документа", "content": {"application/pdf": {}}},
    400: {"description": "Документ не найден: текст ошибки или пустое тело"},
    503: {"description": "Адрес MyNCA, Office или MySpace не настроен"},
}
CALLBACK_RESPONSES = {
    200: {
        "description": "Принято, в том числе повтор",
        "content": {"application/json": {"example": {"status": True}}},
    },
    403: {
        "description": "Неверный ключ в адресе",
        "content": {
            "application/json": {
                "example": {"status": False, "error": "Колбэк не от MyNCA: неверный ключ в адресе"}
            }
        },
    },
    409: {
        "description": "Процесс уже записан на другой документ, подпись не записана",
        "content": {
            "application/json": {
                "example": {
                    "status": False,
                    "error": "Процесс подписания уже записан на другой документ",
                }
            }
        },
    },
    422: {
        "description": "`sign_process_id` или `group_id` не UUID, в базе ничего не меняется",
        "content": {
            "application/json": {"example": {"status": False, "error": "group_id не UUID"}}
        },
    },
    500: {
        "description": "Подпись не записана, MyNCA откатывает её",
        "content": {"application/json": {"example": {"status": False, "error": "..."}}},
    },
    503: {"description": "`CLIENT_SIGN_CALLBACK_SECRET` не настроен"},
}
