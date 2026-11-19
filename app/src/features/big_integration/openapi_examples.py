"""Scalar examples of the routes moved from PHP. Shapes are the real PHP responses."""

_ENVELOPE_ERROR = {
    "data": None,
    "response": False,
    "error": {"message": "Текст ошибки из функции БД"},
}


_UNAUTHORIZED = {"description": "Нет учётки Basic или она неверная. Тело пустое, как в PHP"}


def _responses(data: object) -> dict[int | str, dict[str, object]]:
    return {
        401: _UNAUTHORIZED,
        200: {
            "description": "Успех",
            "content": {
                "application/json": {
                    "example": {"data": data, "response": True, "error": {"message": ""}},
                },
            },
        },
        500: {
            "description": "Ошибка функции БД",
            "content": {"application/json": {"example": _ENVELOPE_ERROR}},
        },
    }


def query_parameters(*names: str) -> dict[str, object]:
    return {
        "parameters": [
            {"name": name, "in": "query", "required": False, "schema": {"type": "string"}}
            for name in names
        ],
    }


def json_body(example: dict[str, object]) -> dict[str, object]:
    return {
        "requestBody": {
            "required": True,
            "content": {"application/json": {"example": example}},
        },
    }


REQUEST_INFO_RESPONSES = _responses(
    {
        "client_request_id": 3219655,
        "client_fio": "Иванов Иван",
        "order_id": "318796",
        "application_id": None,
        "deal_id": None,
        "preset_id": 428,
        "preset_guid": "45989137-3e46-11f1-a841-001dd8b72708",
        "preset_name": "BIG+ Basic 2.0",
    },
)
DDU_REQUEST_INFO_RESPONSES = _responses(
    {
        "client_request_id": 3219655,
        "flat_guid": "c0f17482-ca65-4e58-8446-d1b155f45945",
        "ddu_order_id": "318796",
        "ddu_request_status": "DEAL_CREATED",
        "clean_price": "11871723",
        "request_date": "2026-09-09 09:01:44",
    },
)
REQUEST_CONSTRUCTIVES_RESPONSES = _responses(
    {
        "placementUUID": "c0f17482-ca65-4e58-8446-d1b155f45945",
        "preset_id": 428,
        "preset_name": "BIG+ Basic 2.0",
        "material_list": [],
        "filling_list": [],
    },
)
REMONT_PRESET_RESPONSES = _responses(
    {"placementUUID": "c0f17482-ca65-4e58-8446-d1b155f45945", "preset_list": []},
)
PLACEMENT_BODY = json_body({"placementUUID": "c0f17482-ca65-4e58-8446-d1b155f45945"})
PLACEMENT_PRESET_BODY = json_body(
    {"placementUUID": "c0f17482-ca65-4e58-8446-d1b155f45945", "preset_id": 428},
)
BIG_NOTIFY_CLIENT_RESPONSES = {
    401: _UNAUTHORIZED,
    200: {
        "description": "Успех",
        "content": {
            "application/json": {
                "example": {"data": "OK", "response": True, "error": {"code": 0, "message": ""}},
            },
        },
    },
    400: {
        "description": "Ошибка",
        "content": {
            "application/json": {
                "example": {
                    "data": {},
                    "response": False,
                    "error": {"code": 400, "message": "Текст ошибки"},
                },
            },
        },
    },
}


def _response_json_responses(data: object) -> dict[int | str, dict[str, object]]:
    return {
        401: _UNAUTHORIZED,
        200: {
            "description": "Успех",
            "content": {
                "application/json": {
                    "example": {
                        "data": data,
                        "response": True,
                        "error": {"code": 0, "message": ""},
                    },
                },
            },
        },
        400: {
            "description": "Ошибка функции БД или метод не POST",
            "content": {
                "application/json": {
                    "example": {
                        "data": "P400",
                        "response": False,
                        "error": {"code": 400, "message": "ФИО не может быть пустым"},
                    },
                },
            },
        },
    }


REQUEST_FORM_RESPONSES = _response_json_responses(0)
GET_CONSTRUCTIVES_RESPONSES = _response_json_responses(
    {
        "application_id": 3219655,
        "material_list": [
            {
                "material_name": "Материал / Материал",
                "section": "Бөлім/Раздел",
                "room_name": "Бөлме/Комната",
            },
        ],
    },
)
