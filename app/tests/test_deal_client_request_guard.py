from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from src.features.factoring.ff.service import FactoringService
from src.features.factoring.schemas import PrescoringFactoringRequest
from src.features.installment.deal_guard import check_deal_client_request_state
from src.features.installment.ff.service import FFService
from src.features.installment.schemas import CreateInstallmentApplicationRequest


def test_main_confirmed_request_passes():
    check_deal_client_request_state({"crm_deal_id": 99412, "status_code": "CREATE"}, 2910146)


def test_missing_request_is_404():
    with pytest.raises(HTTPException) as exc:
        check_deal_client_request_state(None, 1)
    assert exc.value.status_code == 404


def test_non_main_request_is_rejected():
    with pytest.raises(HTTPException) as exc:
        check_deal_client_request_state({"crm_deal_id": None, "status_code": "CREATE"}, 2309022)
    assert exc.value.status_code == 409
    assert "основной" in exc.value.detail


def test_unconfirmed_request_is_rejected():
    with pytest.raises(HTTPException) as exc:
        check_deal_client_request_state({"crm_deal_id": 108848, "status_code": None}, 3042042)
    assert exc.value.status_code == 409
    assert "не подтверждён" in exc.value.detail


@pytest.mark.parametrize("status_code", ["CANCEL", "TERMINATED"])
def test_closed_request_is_rejected(status_code):
    with pytest.raises(HTTPException) as exc:
        check_deal_client_request_state({"crm_deal_id": 1, "status_code": status_code}, 1)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_factoring_prescoring_stops_before_bank_on_draft_request():
    repository = MagicMock()
    repository.get_deal_client_request_state = AsyncMock(
        return_value={"crm_deal_id": 108848, "status_code": None}
    )
    svc = FactoringService(repository=repository, client=MagicMock(), app_env="test")
    svc._require_provider = AsyncMock(return_value=MagicMock())
    svc._run_prescoring = AsyncMock()

    request = PrescoringFactoringRequest(
        client_request_id=3042042,
        iin="040516551071",
        mobile_phone="+77001234567",
        principal=Decimal("1000000"),
    )
    with pytest.raises(HTTPException) as exc:
        await svc.run_prescoring(request)

    assert exc.value.status_code == 409
    svc._run_prescoring.assert_not_awaited()


@pytest.mark.asyncio
async def test_installment_create_stops_before_insert_on_non_main_request():
    repository = MagicMock()
    repository.get_deal_client_request_state = AsyncMock(
        return_value={"crm_deal_id": None, "status_code": "CREATE"}
    )
    repository.insert_application = AsyncMock()
    svc = FFService(ff_repository=repository, ff_client=MagicMock(), app_env="test")
    svc._require_provider = AsyncMock(return_value=MagicMock())
    svc._require_webhook_credentials = AsyncMock()

    request = CreateInstallmentApplicationRequest(
        provider_code="FF",
        client_request_id=2309022,
        bank_id=1,
        product_id="p1",
        repayment_method="INSTALLMENT",
        principal=Decimal("1000000"),
        period=12,
        created_by=42,
        iin="040516551071",
        mobile_phone="+77001234567",
    )
    with pytest.raises(HTTPException) as exc:
        await svc.create_application(request)

    assert exc.value.status_code == 409
    repository.insert_application.assert_not_awaited()
