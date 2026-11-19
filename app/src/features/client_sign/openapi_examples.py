"""Scalar examples. Shapes are the real PHP responses, with made-up values."""

PAGE_RESPONSES = {
    200: {
        "description": "HTML как в PHP",
        "content": {"text/html": {"example": "<!doctype html>..."}},
    },
}
_BINARY_FILE = {"schema": {"type": "string", "format": "binary"}}
_ERROR_TEXT = {"text/html": {"example": "Ошибка: Документ не найден"}}


def _file_responses(content_type: str, what: str) -> dict[int, dict[str, object]]:
    return {
        200: {
            "description": (
                f"{what} вложением (`Content-Disposition: attachment`). Если файла нет: страница с "
                "текстом `Ошибка: ...`, код тоже 200, как в PHP"
            ),
            "content": {content_type: _BINARY_FILE, **_ERROR_TEXT},
        },
        302: {
            "description": "Только `doc_type=AGREEMENT_MANUAL`: переход на файл",
            "headers": {"Location": {"schema": {"type": "string"}}},
        },
        503: {"description": "Адрес партнёра или учётка MyNCA не настроены"},
    }


PDF_FILE_RESPONSES = _file_responses("application/pdf", "PDF документа")
CMS_FILE_RESPONSES = _file_responses("application/pkcs7-signature", "Файл CMS подписи")
PERFORM_SIGN_RESPONSES = {
    302: {
        "description": "Переход на страницу подписания MyNCA",
        "headers": {
            "Location": {
                "schema": {"type": "string"},
                "example": "https://mynca.example.invalid/sign/2b1f3c1e-5a8e-4f7c-9d11-4f7a9b1c2d3e",
            }
        },
    },
    200: {
        "description": "Ошибка подписания: страница с текстом, код 200, как в PHP",
        "content": {
            "text/html": {
                "example": (
                    '<div style="padding:40px;font-family:sans-serif;">'
                    '<h3 style="color:#c00;">Ошибка подписания</h3>'
                    "<p>Ошибка создания процесса подписания: текст ошибки</p></div>"
                )
            }
        },
    },
    503: {"description": "Адрес MyNCA, адрес сервиса или секрет колбэка не настроены"},
}
INTEGRATION_FILE_RESPONSES = {
    200: {
        "description": "PDF документа вложением. При `is_base64`: тот же файл текстом в base64",
        "content": {"application/pdf": _BINARY_FILE, "text/html": {"example": "JVBERi0xLjQK..."}},
    },
    400: {"description": "Документ не найден: текст ошибки или пустое тело"},
    503: {"description": "Адрес MyNCA, Office или MySpace не настроен"},
}


def address_parameters(*names: str) -> dict[str, object]:
    """Zend reads a value from the query or from the address: ``/name/value``."""
    return {
        "parameters": [
            {
                "name": name,
                "in": "query",
                "required": False,
                "description": f"В строке запроса или в адресе: `/{name}/значение`",
                "schema": {"type": "string"},
            }
            for name in names
        ],
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
