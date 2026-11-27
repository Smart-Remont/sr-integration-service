"""OpenAPI / Swagger examples for installment FF endpoints."""

CREATE_APPLICATION_REQUEST = {
    "client_request_id": 2916069,
    "provider_code": "FF",
    "iin": "891026301046",
    "mobile_phone": "+77066078118",
    "product_id": "MECHTA_INST",
    "bank_id": 42,
    "repayment_method": "INSTALLMENT",
    "loan_type": "installment",
    "principal": 1500000,
    "period": 12,
    "created_by": 42,
}

CREATE_APPLICATION_RESPONSE = {
    "id": 1,
    "uuid": "fc58a802-ce40-4c8b-9cff-9f11930e1702",
    "reference_id": "1",
    "status": "IN_PROGRESS",
    "redirect_url": "https://loan-cash-superapp.example/goods-loan?uuid=fc58a802-ce40-4c8b-9cff-9f11930e1702",
    "provider_code": "FF",
}

SYNC_PRODUCTS_RESPONSE = {
    "inserted": 3,
    "closed": 0,
    "unchanged": 0,
    "ids": [1, 2, 3],
}

PROVIDER_PRODUCTS_LIST_RESPONSE = {
    "items": [
        {
            "id": 1,
            "provider_code": "FF",
            "provider_product_id": "MECHTA_INST",
            "period": 6,
            "principal_min": 20000,
            "principal_max": 200000,
            "repayment_method": "INSTALLMENT",
            "tier_index": 0,
            "content_hash": "a1b2c3d4e5f6",
            "valid_from": "2026-06-26T10:00:00+00:00",
            "valid_to": None,
            "created_at": "2026-06-26T10:00:00+00:00",
        },
        {
            "id": 2,
            "provider_code": "FF",
            "provider_product_id": "MECHTA_INST",
            "period": 12,
            "principal_min": 20000,
            "principal_max": 200000,
            "repayment_method": "INSTALLMENT",
            "tier_index": 0,
            "content_hash": "b2c3d4e5f6a1",
            "valid_from": "2026-06-26T10:00:00+00:00",
            "valid_to": None,
            "created_at": "2026-06-26T10:00:00+00:00",
        },
    ],
    "total": 2,
}

FF_PRODUCTS_RESPONSE = {
    "partner_id": "SMART_REMONT",
    "name": "TOO SmartRemont",
    "products": [
        {
            "product_id": "MECHTA_INST",
            "repayment_method": "INSTALLMENT",
            "repayment": [
                {"periods": [6, 12], "principal_limits": {"min": 20000, "max": 200000}},
                {"periods": [24], "principal_limits": {"min": 200000, "max": 2000000}},
            ],
        },
    ],
}

APPLICATION_RESPONSE = {
    "id": 1,
    "client_request_id": 2916069,
    "provider_code": "FF",
    "uuid": "fc58a802-ce40-4c8b-9cff-9f11930e1702",
    "reference_id": "1",
    "product_id": "SR_MOCK_FACT_12",
    "loan_type": "installment",
    "principal": 1500000,
    "period": 12,
    "status": "APPROVED",
    "approved_params": {
        "principal": 1500000,
        "period": 12,
        "interest_rate": 0,
        "monthly_payment": 125000,
    },
    "redirect_url": "https://loan-cash-superapp.example/goods-loan?uuid=fc58a802-ce40-4c8b-9cff-9f11930e1702",
    "created_by": 42,
    "bank_id": 42,
    "bank_name": "Freedom Finance Online",
    "installment_product_id": 1,
    "client_request_credit_detail_id": None,
    "created_at": "2026-06-17T10:00:00+00:00",
    "updated_at": "2026-06-17T10:05:00+00:00",
}

WEBHOOK_APPROVED = {
    "uuid": "fc58a802-ce40-4c8b-9cff-9f11930e1702",
    "reference_id": "1",
    "status": "APPROVED",
    "loan_type": "installment",
    "product": "SR_MOCK_FACT_12",
    "approved_params": {
        "principal": 1500000,
        "period": 12,
        "interest_rate": 0,
        "effective_rate": 0,
        "monthly_payment": 125000,
    },
    "alternative_reason": "",
}

WEBHOOK_REJECTED = {
    "uuid": "fc58a802-ce40-4c8b-9cff-9f11930e1702",
    "reference_id": "1",
    "status": "REJECTED",
    "loan_type": "installment",
    "product": "SR_MOCK_FACT_12",
    "approved_params": {
        "principal": 1500000,
        "period": 12,
        "interest_rate": 0,
        "monthly_payment": 0,
    },
    "alternative_reason": "Скоринг отказал",
}

WEBHOOK_ISSUED = {
    "uuid": "fc58a802-ce40-4c8b-9cff-9f11930e1702",
    "reference_id": "1",
    "status": "ISSUED",
    "loan_type": "installment",
    "product": "SR_MOCK_FACT_12",
    "approved_params": {
        "principal": 1500000,
        "period": 12,
        "interest_rate": 0,
        "monthly_payment": 125000,
    },
}

WEBHOOK_ACK_RESPONSE = {"ok": True, "status": True}

# Route-level OpenAPI fragments (Swagger shows these in Try it out / Examples dropdown)
CREATE_APPLICATION_BODY = {
    "installment": {
        "summary": "Рассрочка на ремонт",
        "description": "ИИН и телефон заёмщика — в теле запроса, могут отличаться от владельца client_request.",
        "value": CREATE_APPLICATION_REQUEST,
    },
}

CREATE_APPLICATION_RESPONSES = {
    201: {
        "description": "Заявка создана, клиент переходит по redirect_url",
        "content": {
            "application/json": {
                "examples": {
                    "in_progress": {
                        "summary": "Отправлено в FF",
                        "value": CREATE_APPLICATION_RESPONSE,
                    },
                },
            },
        },
    },
}

SYNC_PRODUCTS_RESPONSES = {
    200: {
        "description": "Результат append-only sync в installment_provider_product_tab",
        "content": {
            "application/json": {
                "examples": {
                    "first_sync": {
                        "summary": "Первый sync (3 exploded rows)",
                        "value": SYNC_PRODUCTS_RESPONSE,
                    },
                },
            },
        },
    },
}

PROVIDER_PRODUCTS_RESPONSES = {
    200: {
        "description": "Актуальные строки каталога провайдера",
        "content": {
            "application/json": {
                "examples": {
                    "ff_current": {
                        "summary": "FF — текущие продукты",
                        "value": PROVIDER_PRODUCTS_LIST_RESPONSE,
                    },
                },
            },
        },
    },
}

FF_PRODUCTS_RESPONSES = {
    200: {
        "description": "Каталог продуктов партнёра из FF get-partner-info",
        "content": {
            "application/json": {
                "examples": {
                    "mock": {
                        "summary": "Mock seed (SR_MOCK_*)",
                        "value": FF_PRODUCTS_RESPONSE,
                    },
                },
            },
        },
    },
}

APPLICATION_RESPONSES = {
    200: {
        "description": "Текущее состояние заявки в БД",
        "content": {
            "application/json": {
                "examples": {
                    "approved": {
                        "summary": "Одобрено (после webhook)",
                        "value": APPLICATION_RESPONSE,
                    },
                },
            },
        },
    },
}

WEBHOOK_ACK_RESPONSES = {
    200: {
        "description": "Webhook принят (идемпотентно при повторе)",
        "content": {
            "application/json": {
                "examples": {
                    "ok": {"value": WEBHOOK_ACK_RESPONSE},
                },
            },
        },
    },
}

BIF_CREATE_APPLICATION_REQUEST = {
    "client_request_id": 2916069,
    "provider_code": "BIF",
    "bank_id": 101,
    "iin": "900101300123",
    "mobile_phone": "+77010000000",
    "principal": 1000000,
    "period": 12,
    "initial_fee": 0,
    "payment_day": 10,
    "created_by": 42,
}

BIF_CREATE_APPLICATION_RESPONSE = {
    "id": 15,
    "uuid": "1042",
    "reference_id": "SR-15",
    "status": "IN_PROGRESS",
    "redirect_url": None,
    "redirect_qr": None,
    "provider_code": "BIF",
}

BIF_WEBHOOK_APPROVED = {
    "appId": "1042",
    "status": "APPROVED",
    "approvedConditions": {
        "amount": 1000000,
        "termMonths": 12,
        "rate": 24.9,
        "monthlyPayment": 95000,
    },
    "comment": "",
}

BIF_WEBHOOK_ISSUED = {"appId": "1042", "status": "ISSUED"}

MANUAL_REFUND_REQUEST = {
    "amount": 1000000,
    "comment": "Заявление №12 от 20.11, перевод п/п 345",
    "created_by": 42,
    "refunded_at": "2026-11-24",
}

OA_STATS_RESPONSE = {
    "providers": [
        {
            "provider_code": "BIF",
            "total_count": 3,
            "total_amount": 3500000,
            "by_status": [
                {"status": "ISSUED", "count": 1, "amount": 1000000},
                {"status": "REJECTED", "count": 2, "amount": 2500000},
            ],
            "issued_count": 1,
            "issued_amount": 1000000,
            "conversion": 0.3333,
            "refunded_count": 0,
            "refunded_amount": 0,
        }
    ],
    "total": {
        "total_count": 3,
        "total_amount": 3500000,
        "by_status": [
            {"status": "ISSUED", "count": 1, "amount": 1000000},
            {"status": "REJECTED", "count": 2, "amount": 2500000},
        ],
        "issued_count": 1,
        "issued_amount": 1000000,
        "conversion": 0.3333,
        "refunded_count": 0,
        "refunded_amount": 0,
    },
}
