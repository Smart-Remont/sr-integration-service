"""Scalar examples."""

BUH_REQUEST: dict[str, object] = {
    "parameters": [
        {
            "name": "what",
            "in": "query",
            "description": "`lot`, `lot_guid`, `material_guid`, `warehouse_guid`",
            "schema": {"type": "string"},
            "example": "warehouse_guid",
        }
    ],
    "requestBody": {
        "content": {
            "application/json": {
                "example": {"warehouse": [{"warehouse_id": 4, "warehouse_guid": "guid"}]}
            }
        }
    },
}

BUH_RESPONSES: dict[int | str, dict[str, object]] = {
    200: {
        "description": (
            "Значение функции как есть. Пусто, если `what` не был ветвью PHP или функция дала null"
        ),
        "content": {
            "application/json": {
                "example": {
                    "warehouse": [
                        {
                            "warehouse_id": 4,
                            "warehouse_guid": "guid",
                            "status": "OK",
                            "error": "",
                        }
                    ]
                }
            }
        },
    },
    400: {
        "description": "Функция БД не выполнилась, текст как в PHP",
        "content": {"application/json": {"example": {"error": "Текст"}}},
    },
    401: {"description": "Нет учётки Basic или она неверная, тело пустое"},
    410: {
        "description": "Этот `what` есть в PHP, но не перенесён: база не вызывалась",
        "content": {
            "application/json": {
                "example": {"error": "what=provider не перенесён в сервис, вызов ничего не сделал"}
            }
        },
    },
    503: {"description": "Не заданы `BUH_1C_*`"},
}
