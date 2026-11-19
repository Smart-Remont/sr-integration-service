CREATE_APPLICATION_REQUEST = {
    "client_request_id": 2916069,
    "iin": "891026301046",
    "mobile_phone": "+77066078118",
    "principal": 119890,
    "period": 12,
    "created_by": 42,
    "branch_code": "200000",
    "print_forms": [
        {"name": "application", "url": "https://example.test/signed/application.pdf"},
        {"name": "notification", "url": "https://example.test/signed/notification.pdf"},
    ],
}

CREATE_APPLICATION_RESPONSE = {
    "id": 1,
    "uuid": "fc58a802-ce40-4c8b-9cff-9f11930e1702",
    "reference_id": "1",
    "credit_contract": "FCT26-200000-1",
    "status": "IN_PROGRESS",
    "redirect_url": "https://loan-cash-superapp.ffb-stage.freedombank.kz/...",
    "provider_code": "FF_FACTORING",
}

APPLICATION_RESPONSE = {
    "id": 1,
    "client_request_id": 2916069,
    "provider_code": "FF_FACTORING",
    "uuid": "fc58a802-ce40-4c8b-9cff-9f11930e1702",
    "reference_id": "1",
    "credit_contract": "FCT26-200000-1",
    "product_id": "FACTORING_SR",
    "partner": "FACTORING_AZ",
    "channel": "AVIATA_FAC_WEB",
    "principal": 119890,
    "period": 12,
    "status": "IN_PROGRESS",
    "redirect_url": "https://loan-cash-superapp.ffb-stage.freedombank.kz/...",
    "issued_at": None,
    "created_by": 42,
}

CANCEL_APPLICATION_REQUEST = {"cancelled_by": 42, "reason": "Клиент передумал"}

WEBHOOK_APPROVED = {
    "uuid": "fc58a802-ce40-4c8b-9cff-9f11930e1702",
    "reference_id": "1",
    "status": "APPROVED",
}

WEBHOOK_REJECTED = {
    "uuid": "fc58a802-ce40-4c8b-9cff-9f11930e1702",
    "reference_id": "1",
    "status": "REJECTED",
}

WEBHOOK_ISSUED = {
    "uuid": "fc58a802-ce40-4c8b-9cff-9f11930e1702",
    "reference_id": "1",
    "status": "ISSUED",
}

WEBHOOK_ACK_RESPONSE = {"ok": True, "status": True}

CREATE_APPLICATION_BODY = {
    "default": {
        "summary": "Factoring apply",
        "value": CREATE_APPLICATION_REQUEST,
    }
}

CREATE_APPLICATION_RESPONSES = {
    201: {"description": "Created"},
    422: {"description": "Validation error"},
    502: {"description": "Bank error"},
}

APPLICATION_RESPONSES = {
    200: {"description": "OK"},
    404: {"description": "Not found"},
}

CANCEL_APPLICATION_RESPONSES = {
    200: {"description": "Заявка отменена (status=CANCELLED), тело — как у GET заявки"},
    404: {"description": "Заявка не найдена"},
    409: {
        "description": "Отменить нельзя: заявка уже в банке или не в NEW / WAITING_SIGN",
        "content": {
            "application/json": {
                "example": {"detail": "Заявка уже отправлена в банк — отменить нельзя"}
            }
        },
    },
}

WEBHOOK_ACK_RESPONSES = {
    200: {"description": "Ack"},
}
