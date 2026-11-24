from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from src.features.installment.bif.client import BIFClientError, BIFToken
from src.features.installment.bif.service import BIFService, bif_phone
from src.features.installment.ff.service import FFService
from src.features.installment.schemas import CreateInstallmentRefundRequest, ManualRefundRequest
from tests.installment.bif_fakes import application, bif_request, events, repository


def _service(repo: MagicMock | None = None) -> BIFService:
    client = MagicMock()
    client.issue_token = AsyncMock(return_value=BIFToken("access-1", "refresh-1", 600))
    client.refresh_token = AsyncMock(return_value=BIFToken("access-2", "refresh-2", 600))
    client.access_token_expires_at = MagicMock(return_value=None)
    client.create_order = AsyncMock(
        return_value={"code": "SUCCESS", "appId": "1042", "message": "Заявка создана"}
    )
    return BIFService(repo or repository(), client, "stage", repeat_delay_seconds=0)


# --- create -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_sends_order_with_partner_id_and_stores_app_id():
    svc = _service()

    answer = await svc.create_application(bif_request())

    assert answer.uuid == "1042"
    assert answer.reference_id == "SR-15"
    assert answer.status == "IN_PROGRESS"
    assert answer.provider_code == "BIF"
    assert answer.redirect_url is None and answer.redirect_qr is None

    insert = svc.repository.insert_application.await_args.kwargs
    assert insert["product_id"] == "bezzalogovyy-kredit"
    assert insert["mobile_phone"] == "77010000000"
    assert insert["payment_day"] == 10
    assert insert["expires_at"] is not None

    payload = svc.client.create_order.await_args.args[2]
    assert payload["partnerId"] == "SR-15"
    order = payload["orderData"]
    assert order["person"] == {"iin": "900101300123", "mobilePhone": "77010000000"}
    assert order["loanInfo"] == {
        "creditAmount": 1000000,
        "creditTerm": 12,
        "initialFee": 0,
        "paymentDate": "10",
        "repaymentType": "0",
    }
    # Seller of the deal's company (8), not the default one.
    assert order["sellerBin"] == "222222222222"
    assert order["schemeDefPayment"] == "0" and order["isDeferredPrincipal"] is False
    assert svc.client.create_order.await_args.args[1] == "access-1"

    after = svc.repository.update_application_after_apply.await_args.kwargs
    assert after["uuid"] == "1042" and after["status"] == "IN_PROGRESS"
    assert "BIF_APPLY_REQUEST" in events(svc.repository)
    # The event log does not keep the borrower's IIN and phone.
    logged = next(
        call.kwargs["payload"]
        for call in svc.repository.insert_event_log.await_args_list
        if call.kwargs["event_type"] == "BIF_APPLY_REQUEST"
    )
    assert logged["orderData"]["person"] == {"iin": "***", "mobilePhone": "***"}


@pytest.mark.asyncio
async def test_create_uses_cached_token():
    repo = repository()
    repo.get_token = AsyncMock(
        return_value=MagicMock(
            access_token="cached",
            refresh_token="r",
            expires_at=datetime(2999, 1, 1, tzinfo=UTC),
        )
    )
    svc = _service(repo)

    await svc.create_application(bif_request())

    svc.client.issue_token.assert_not_awaited()
    assert svc.client.create_order.await_args.args[1] == "cached"


@pytest.mark.asyncio
async def test_bank_refusal_message_goes_to_detail_and_application_fails():
    svc = _service()
    message = (
        "Продукт не подходит под условия сделки: Сумма вне границ продукта (AMOUNT_OUT_OF_RANGE)"
    )
    svc.client.create_order = AsyncMock(
        side_effect=BIFClientError(400, message, body={"status": 400, "message": message})
    )

    with pytest.raises(HTTPException) as exc:
        await svc.create_application(bif_request())

    assert exc.value.status_code == 422
    assert exc.value.detail == message
    svc.repository.mark_apply_failed.assert_awaited_once_with(15)
    assert "BIF_APPLY_FAILED" in events(svc.repository)


@pytest.mark.asyncio
async def test_active_application_at_bank_is_409():
    svc = _service()
    svc.client.create_order = AsyncMock(
        side_effect=BIFClientError(409, "Активная заявка уже есть", body={"status": 409})
    )

    with pytest.raises(HTTPException) as exc:
        await svc.create_application(bif_request())

    assert exc.value.status_code == 409
    assert "Активная заявка уже есть" in exc.value.detail


@pytest.mark.asyncio
async def test_timeout_is_repeated_with_same_partner_id_then_unknown():
    svc = _service()
    svc.client.create_order = AsyncMock(
        side_effect=BIFClientError(504, "BI Finance не ответил", maybe_sent=True)
    )

    with pytest.raises(HTTPException) as exc:
        await svc.create_application(bif_request())

    assert exc.value.status_code == 504
    assert svc.client.create_order.await_count == 2
    partner_ids = {call.args[2]["partnerId"] for call in svc.client.create_order.await_args_list}
    assert partner_ids == {"SR-15"}
    svc.repository.mark_apply_unknown.assert_awaited_once_with(15)
    svc.repository.mark_apply_failed.assert_not_awaited()


@pytest.mark.asyncio
async def test_repeat_after_timeout_answered_by_409_with_app_id_is_success():
    svc = _service()
    svc.client.create_order = AsyncMock(
        side_effect=[
            BIFClientError(502, "Bad gateway", maybe_sent=True),
            BIFClientError(409, "Заявка уже создана", body={"appId": "1042"}),
        ]
    )

    answer = await svc.create_application(bif_request())

    assert answer.uuid == "1042"
    svc.repository.mark_apply_failed.assert_not_awaited()


@pytest.mark.asyncio
async def test_unanswered_application_goes_again_without_new_insert():
    repo = repository()
    repo.get_applications_by_client_request = AsyncMock(
        return_value=[application(id=21, uuid=None, reference_id="SR-21", status="APPLY_UNKNOWN")]
    )
    svc = _service(repo)

    answer = await svc.create_application(bif_request())

    repo.insert_application.assert_not_awaited()
    assert svc.client.create_order.await_args.args[2]["partnerId"] == "SR-21"
    assert answer.id == 21
    assert "BIF_APPLY_REPEAT" in events(repo)


@pytest.mark.asyncio
async def test_unanswered_application_on_other_terms_is_409():
    repo = repository()
    repo.get_applications_by_client_request = AsyncMock(
        return_value=[application(id=21, uuid=None, status="APPLY_UNKNOWN")]
    )
    svc = _service(repo)

    with pytest.raises(HTTPException) as exc:
        await svc.create_application(bif_request(principal=2000000))

    assert exc.value.status_code == 409
    svc.client.create_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_401_renews_token_once():
    svc = _service()
    svc.client.create_order = AsyncMock(
        side_effect=[BIFClientError(401, "expired"), {"appId": "1042"}]
    )

    answer = await svc.create_application(bif_request())

    assert answer.uuid == "1042"
    assert svc.client.create_order.await_count == 2


@pytest.mark.asyncio
async def test_refresh_token_used_when_access_expired():
    repo = repository()
    repo.get_token = AsyncMock(
        return_value=MagicMock(
            access_token="old",
            refresh_token="refresh-old",
            expires_at=datetime(2000, 1, 1, tzinfo=UTC),
        )
    )
    svc = _service(repo)

    await svc.create_application(bif_request())

    svc.client.refresh_token.assert_awaited_once()
    svc.client.issue_token.assert_not_awaited()
    assert repo.upsert_token.await_args.kwargs["access_token"] == "access-2"


@pytest.mark.asyncio
async def test_no_hook_login_no_application():
    repo = repository()
    repo.get_provider_webhook_credentials = AsyncMock(return_value=None)
    svc = _service(repo)

    with pytest.raises(HTTPException) as exc:
        await svc.create_application(bif_request())

    assert exc.value.status_code == 503
    repo.insert_application.assert_not_awaited()


@pytest.mark.asyncio
async def test_default_seller_when_company_not_configured():
    repo = repository()
    repo.get_deal_company_id = AsyncMock(return_value=1)
    svc = _service(repo)

    await svc.create_application(bif_request())

    assert svc.client.create_order.await_args.args[2]["orderData"]["sellerBin"] == "333333333333"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+77010000000", "77010000000"),
        ("87010000000", "77010000000"),
        ("7010000000", "77010000000"),
        ("+7 (701) 000-00-00", "77010000000"),
        ("+79161234567", None),
        ("123", None),
    ],
)
def test_bif_phone(raw, expected):
    assert bif_phone(raw) == expected


# --- manual refund and expiry -------------------------------------------------------


@pytest.mark.asyncio
async def test_manual_refund_returns_updated_application():
    repo = repository()
    repo.get_application_by_id = AsyncMock(
        side_effect=[
            application(status="ISSUED"),
            application(status="REVERSED", refund_mode="MANUAL", refund_status="SUCCESS"),
        ]
    )
    svc = _service(repo)

    answer = await svc.mark_manual_refund(
        15,
        ManualRefundRequest(
            amount=Decimal(1000000),
            comment="Заявление №12",
            created_by=7,
            refunded_at=date(2026, 11, 20),
        ),
    )

    assert answer.status == "REVERSED" and answer.refund_mode == "MANUAL"
    kwargs = repo.refund_manual.await_args.kwargs
    assert kwargs["amount"] == Decimal(1000000)
    assert kwargs["refunded_at"] == date(2026, 11, 20)


@pytest.mark.asyncio
async def test_manual_refund_of_freedom_is_409():
    repo = repository()
    repo.get_application_by_id = AsyncMock(return_value=application(provider_code="FF"))
    svc = _service(repo)

    with pytest.raises(HTTPException) as exc:
        await svc.mark_manual_refund(
            15, ManualRefundRequest(amount=Decimal(1), comment="x", created_by=7)
        )

    assert exc.value.status_code == 409
    repo.refund_manual.assert_not_awaited()


@pytest.mark.asyncio
async def test_manual_refund_unknown_application_is_404():
    repo = repository()
    repo.get_application_by_id = AsyncMock(return_value=None)
    svc = _service(repo)

    with pytest.raises(HTTPException) as exc:
        await svc.mark_manual_refund(
            99, ManualRefundRequest(amount=Decimal(1), comment="x", created_by=7)
        )

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_expire_returns_count():
    svc = _service()

    answer = await svc.expire_applications(limit=500)

    assert answer.expired == 2 and answer.ids == [3, 4]
    svc.repository.expire_applications.assert_awaited_once_with(limit=500)


# --- Freedom-only routes refuse BI Finance ------------------------------------------


def _ff_service(app_provider: str) -> FFService:
    repo = repository()
    repo.get_application_by_id = AsyncMock(return_value=application(provider_code=app_provider))
    return FFService(ff_repository=repo, ff_client=MagicMock(), app_env="test")


@pytest.mark.asyncio
async def test_poll_of_bif_is_409():
    with pytest.raises(HTTPException) as exc:
        await _ff_service("BIF").poll_application(15)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_bank_refund_of_bif_is_409():
    with pytest.raises(HTTPException) as exc:
        await _ff_service("BIF").create_refund(15, CreateInstallmentRefundRequest(requested_by=7))
    assert exc.value.status_code == 409
