from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from src.features.factoring.ff.service import FactoringService
from src.features.factoring.schemas import FactoringApplicationResponse


def _application(**overrides) -> FactoringApplicationResponse:
    base = {
        "id": 1,
        "client_request_id": 100,
        "provider_code": "FF_FACTORING",
        "uuid": "bank-uuid",
        "reference_id": "1",
        "credit_contract": "FCT",
        "status": "ISSUED",
        "principal": Decimal("100000"),
        "refund_status": "REQUESTED",
        "refund_amount": None,
    }
    base.update(overrides)
    return FactoringApplicationResponse(**base)


def test_refund_webhook_requires_requested_state():
    app = _application(refund_status=None)
    with pytest.raises(HTTPException) as exc:
        FactoringService._require_refund_webhook_transition(
            app,
            covlir_status="SUCCESS",
            refund_amount=Decimal("1000"),
        )
    assert exc.value.status_code == 409


def test_refund_webhook_caps_amount_at_principal():
    app = _application(refund_amount=Decimal("90000"))
    with pytest.raises(HTTPException) as exc:
        FactoringService._require_refund_webhook_transition(
            app,
            covlir_status="SUCCESS",
            refund_amount=Decimal("20000"),
        )
    assert exc.value.status_code == 422


def test_refund_webhook_allows_valid_success_transition():
    app = _application(refund_amount=Decimal("20000"))
    FactoringService._require_refund_webhook_transition(
        app,
        covlir_status="SUCCESS",
        refund_amount=Decimal("30000"),
    )


def test_refund_webhook_idempotent_success():
    app = _application(refund_status="SUCCESS")
    assert FactoringService._refund_webhook_idempotent(app, "SUCCESS") is True


def test_refund_webhook_not_idempotent_when_requested():
    app = _application(refund_status="REQUESTED")
    assert FactoringService._refund_webhook_idempotent(app, "SUCCESS") is False
