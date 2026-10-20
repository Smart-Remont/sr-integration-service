import base64
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import HTTPException
from src.features.installment.ff.client import FFClient, FFClientError
from src.features.installment.ff.repo import (
    FFProvider,
    FFWebhookApplication,
    FFWebhookCredential,
)
from src.features.installment.ff.service import FFService
from src.features.installment.schemas import (
    CreateInstallmentApplicationRequest,
    FFProductsResponse,
    InstallmentApplicationResponse,
)

_PROVIDER = FFProvider(
    id=7,
    code="FF",
    base_url="https://ff.example",
    config={
        "partner_id": "SR_PARTNER",
        "channel": "SR_CHANNEL",
        "hook_url": "https://sr.example/hook",
        "success_url": "https://sr.example/ok",
        "failure_url": "https://sr.example/fail",
    },
)


def _request(**overrides) -> CreateInstallmentApplicationRequest:
    defaults = {
        "client_request_id": 3041810,
        "provider_code": "FF",
        "iin": "891026301046",
        "mobile_phone": "87066078118",
        "product_id": "SR_INS",
        "bank_id": 42,
        "repayment_method": "INSTALLMENT",
        "principal": Decimal(500000),
        "period": 12,
        "created_by": 7,
    }
    defaults.update(overrides)
    return CreateInstallmentApplicationRequest(**defaults)


def _application(**overrides) -> InstallmentApplicationResponse:
    now = datetime.now(UTC)
    defaults = {
        "id": 101,
        "client_request_id": 3041810,
        "provider_code": "FF",
        "uuid": "b9ffb5db-f7ec-4c2e-a0ec-f91ff1d2319a",
        "reference_id": "101",
        "status": "IN_PROGRESS",
        "created_by": 7,
        "created_at": now,
        "updated_at": now,
    }
    defaults.update(overrides)
    return InstallmentApplicationResponse(**defaults)


def _service() -> FFService:
    repo = MagicMock()
    repo.get_provider_by_code = AsyncMock(return_value=_PROVIDER)
    repo.get_provider_webhook_credentials = AsyncMock(
        return_value=FFWebhookCredential(username="hook", password_hash="x")
    )
    repo.get_deal_client_request_state = AsyncMock(
        return_value={"status_code": "CONFIRM", "crm_deal_id": 1}
    )
    repo.get_deal_total_amount = AsyncMock(return_value=None)
    repo.get_deal_committed_amount = AsyncMock(return_value=Decimal(0))
    repo.insert_application = AsyncMock(return_value=101)
    repo.insert_event_log = AsyncMock()
    repo.update_application_after_apply = AsyncMock()
    repo.mark_apply_failed = AsyncMock()
    repo.mark_apply_unknown = AsyncMock()
    repo.update_application_from_webhook = AsyncMock()
    repo.update_application_from_poll = AsyncMock()
    repo.apply_application_to_deal = AsyncMock(return_value=555)
    repo.get_token = AsyncMock(return_value=None)
    repo.upsert_token = AsyncMock()
    repo.get_active_credentials = AsyncMock(
        return_value=MagicMock(username="u", password="p")
    )

    async def _committed(action):
        return await action(repo)

    repo.committed = AsyncMock(side_effect=_committed)
    client = MagicMock()
    client.authenticate = AsyncMock(return_value=("access", "refresh"))
    client.access_token_expires_at = MagicMock(return_value=None)
    svc = FFService(ff_repository=repo, ff_client=client, app_env="test")
    svc._verify_webhook_auth = AsyncMock()
    return svc


def _events(svc: FFService) -> list[str]:
    return [
        call.kwargs["event_type"]
        for call in svc.ff_repository.insert_event_log.await_args_list
    ]


# --- create -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_stores_borrower_and_returns_local_qr():
    svc = _service()
    svc.ff_client.apply_goods_loan_lead = AsyncMock(
        return_value={"uuid": "u-1", "url": "https://bank.example/goods-loan?uuid=u-1"}
    )

    result = await svc.create_application(_request())

    kwargs = svc.ff_repository.insert_application.await_args.kwargs
    assert kwargs["iin"] == "891026301046"
    assert kwargs["mobile_phone"] == "+77066078118"
    assert result.uuid == "u-1"
    assert result.redirect_qr and result.redirect_qr.startswith("data:image/svg+xml")


@pytest.mark.asyncio
async def test_create_timeout_marks_unknown_not_failed():
    svc = _service()
    svc.ff_client.apply_goods_loan_lead = AsyncMock(
        side_effect=FFClientError(504, "read timeout", maybe_sent=True)
    )

    with pytest.raises(HTTPException) as exc_info:
        await svc.create_application(_request())

    assert exc_info.value.status_code == 504
    svc.ff_repository.mark_apply_unknown.assert_awaited_once_with(101)
    svc.ff_repository.mark_apply_failed.assert_not_awaited()
    assert "FF_APPLY_UNKNOWN" in _events(svc)


@pytest.mark.asyncio
async def test_create_bank_rejects_fields_marks_failed():
    svc = _service()
    svc.ff_client.apply_goods_loan_lead = AsyncMock(
        side_effect=FFClientError(400, "Обязательное поле.")
    )

    with pytest.raises(HTTPException) as exc_info:
        await svc.create_application(_request())

    assert exc_info.value.status_code == 422
    svc.ff_repository.mark_apply_failed.assert_awaited_once_with(101)
    svc.ff_repository.mark_apply_unknown.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_without_uuid_marks_unknown():
    svc = _service()
    svc.ff_client.apply_goods_loan_lead = AsyncMock(return_value={"success": True})

    with pytest.raises(HTTPException):
        await svc.create_application(_request())

    svc.ff_repository.mark_apply_unknown.assert_awaited_once_with(101)


@pytest.mark.asyncio
async def test_create_relogs_in_once_on_401():
    svc = _service()
    svc.ff_client.apply_goods_loan_lead = AsyncMock(
        side_effect=[FFClientError(401, "token_not_valid"), {"uuid": "u-2"}]
    )

    result = await svc.create_application(_request())

    assert result.uuid == "u-2"
    assert svc.ff_client.authenticate.await_count == 2  # no token, then re-login on 401


# --- webhook ----------------------------------------------------------------


def _hook(**overrides) -> dict:
    payload = {
        "uuid": "b9ffb5db-f7ec-4c2e-a0ec-f91ff1d2319a",
        "credit_contract": "FCT26-200000-6001678",
        "iin": "030524501293",
        "mobile_phone": "+77056785044",
        "first_name": " ЖАНДОС ",
        "last_name": " САПАРБАЕВ ",
        "middle_name": " ОМАРҰЛЫ ",
        "approved_params": {
            "principal": 314990,
            "period": 12,
            "interest_rate": 0,
            "monthly_payment": 0,
        },
        "reference_id": "101",
        "status": "ISSUED",
        "channel": "FACT_MM_SUP",
        "loan_type": "installment",
        "product": "SR_INS",
        "alternative_reason": "",
    }
    payload.update(overrides)
    return payload


def _found(
    status: str = "APPROVED", approved_params: dict | None = None
) -> FFWebhookApplication:
    return FFWebhookApplication(id=101, status=status, approved_params=approved_params)


@pytest.mark.asyncio
async def test_webhook_keeps_borrower_and_contract():
    svc = _service()
    svc.ff_repository.get_application_by_reference_or_uuid = AsyncMock(
        return_value=_found()
    )
    svc.ff_repository.get_application_by_id = AsyncMock(
        side_effect=[
            _application(),
            _application(status="ISSUED", client_request_credit_detail_id=None),
        ]
    )

    await svc.handle_webhook(_hook(), authorization_header="Basic x")

    kwargs = svc.ff_repository.update_application_from_webhook.await_args.kwargs
    params = kwargs["approved_params"]
    assert params["first_name"] == "ЖАНДОС"
    assert params["last_name"] == "САПАРБАЕВ"
    assert params["iin"] == "030524501293"
    assert params["principal"] == 314990
    assert "alternative_reason" not in params
    assert kwargs["credit_contract"] == "FCT26-200000-6001678"
    svc.ff_repository.apply_application_to_deal.assert_awaited_once()


@pytest.mark.asyncio
async def test_webhook_drops_bank_placeholders():
    svc = _service()
    svc.ff_repository.get_application_by_reference_or_uuid = AsyncMock(
        return_value=_found("IN_PROGRESS")
    )
    svc.ff_repository.get_application_by_id = AsyncMock(return_value=_application())

    await svc.handle_webhook(
        _hook(
            status="APPROVED",
            iin="",
            mobile_phone="+",
            first_name="",
            last_name="",
            middle_name="",
        ),
        authorization_header="Basic x",
    )

    params = svc.ff_repository.update_application_from_webhook.await_args.kwargs[
        "approved_params"
    ]
    for key in ("iin", "mobile_phone", "first_name", "last_name", "middle_name"):
        assert key not in params


@pytest.mark.asyncio
async def test_webhook_unknown_status_is_acked_without_update():
    svc = _service()
    svc.ff_repository.get_application_by_reference_or_uuid = AsyncMock(
        return_value=_found("ISSUED")
    )
    svc.ff_repository.get_application_by_id = AsyncMock(
        return_value=_application(status="ISSUED")
    )

    ack = await svc.handle_webhook(
        _hook(status="PARTIAL_REFUND"), authorization_header="Basic x"
    )

    assert ack.ok
    svc.ff_repository.update_application_from_webhook.assert_not_awaited()
    assert "WEBHOOK_UNSUPPORTED_STATUS" in _events(svc)


@pytest.mark.asyncio
async def test_webhook_repeat_is_duplicate():
    svc = _service()
    hook = _hook(status="APPROVED")
    stored = svc._bank_params(hook, params_key="approved_params")
    svc.ff_repository.get_application_by_reference_or_uuid = AsyncMock(
        return_value=_found(
            "APPROVED", approved_params={**stored, "extra_from_poll": 1}
        )
    )
    svc.ff_repository.get_application_by_id = AsyncMock(
        return_value=_application(status="APPROVED")
    )

    await svc.handle_webhook(hook, authorization_header="Basic x")

    svc.ff_repository.update_application_from_webhook.assert_not_awaited()
    assert "HOOK_DUPLICATE" in _events(svc)


@pytest.mark.asyncio
async def test_webhook_does_not_reopen_issued():
    svc = _service()
    svc.ff_repository.get_application_by_reference_or_uuid = AsyncMock(
        return_value=_found("ISSUED")
    )
    svc.ff_repository.get_application_by_id = AsyncMock(
        return_value=_application(status="ISSUED")
    )

    await svc.handle_webhook(
        _hook(status="REJECTED", credit_contract="NEW-1"),
        authorization_header="Basic x",
    )

    assert (
        svc.ff_repository.update_application_from_webhook.await_args.kwargs["status"]
        == "ISSUED"
    )


# --- reconcile --------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_expires_unknown_and_applies_issued():
    svc = _service()
    old = datetime.now(UTC) - timedelta(hours=3)
    fresh = datetime.now(UTC) - timedelta(minutes=30)
    svc.ff_repository.list_for_reconcile = AsyncMock(
        return_value=[
            {
                "id": 1,
                "uuid": None,
                "status": "APPLY_UNKNOWN",
                "created_at": old,
                "updated_at": old,
            },
            {
                "id": 2,
                "uuid": None,
                "status": "APPLY_UNKNOWN",
                "created_at": fresh,
                "updated_at": fresh,
            },
            {
                "id": 3,
                "uuid": "u-3",
                "status": "ISSUED",
                "created_at": old,
                "updated_at": old,
            },
            {
                "id": 4,
                "uuid": "u-4",
                "status": "IN_PROGRESS",
                "created_at": old,
                "updated_at": old,
            },
        ]
    )
    svc.ff_repository.get_application_by_id = AsyncMock(
        side_effect=lambda app_id: _application(
            id=app_id,
            status="ISSUED" if app_id == 3 else "REJECTED",
            uuid=f"u-{app_id}",
        )
    )
    svc.ff_client.get_goods_application_info = AsyncMock(
        return_value={"uuid": "u-4", "status": "REJECTED", "status_reason": "Отказ"}
    )

    result = await svc.reconcile()

    svc.ff_repository.mark_apply_failed.assert_awaited_once_with(1)
    assert result.expired == 1
    assert result.applied == 1
    assert result.polled == 1
    assert result.changed == 1
    assert result.errors == 0


@pytest.mark.asyncio
async def test_reconcile_counts_errors_and_continues():
    svc = _service()
    old = datetime.now(UTC) - timedelta(hours=1)
    svc.ff_repository.list_for_reconcile = AsyncMock(
        return_value=[
            {
                "id": 4,
                "uuid": "u-4",
                "status": "IN_PROGRESS",
                "created_at": old,
                "updated_at": old,
            },
            {
                "id": 5,
                "uuid": "u-5",
                "status": "IN_PROGRESS",
                "created_at": old,
                "updated_at": old,
            },
        ]
    )
    svc.ff_repository.get_application_by_id = AsyncMock(
        side_effect=lambda app_id: _application(id=app_id, uuid=f"u-{app_id}")
    )
    svc.ff_client.get_goods_application_info = AsyncMock(
        side_effect=[
            FFClientError(504, "timeout", maybe_sent=True),
            {"uuid": "u-5", "status": "APPROVED"},
        ]
    )

    result = await svc.reconcile()

    assert result.errors == 1
    assert result.polled == 1


# --- products, client -------------------------------------------------------


def test_products_accept_flat_min_max():
    payload = {
        "partner_id": "P",
        "name": "Партнёр",
        "products": [
            {
                "product_id": "P_INS",
                "repayment_method": "INSTALMENT",
                "repayment": [
                    {"min": 50000.0, "max": 149999.0, "periods": [3, 6]},
                    {
                        "principal_limits": {"min": 150000.0, "max": 499999.0},
                        "periods": [12],
                    },
                ],
            }
        ],
    }

    parsed = FFProductsResponse.model_validate(payload)

    limits = [tier.principal_limits for tier in parsed.products[0].repayment]
    assert limits[0].min == Decimal(50000) and limits[1].max == Decimal(499999)
    assert "principal_limits" in parsed.products[0].repayment[0].model_dump()


def test_server_error_may_have_reached_bank():
    response = httpx.Response(502, json={"message": "Bad gateway"})
    with pytest.raises(FFClientError) as exc_info:
        FFClient._parse_response(response)
    assert exc_info.value.maybe_sent

    response = httpx.Response(400, json={"message": "Сервис временно недоступен"})
    with pytest.raises(FFClientError) as exc_info:
        FFClient._parse_response(response)
    assert not exc_info.value.maybe_sent


def test_access_token_expiry_from_jwt():
    exp = int((datetime.now(UTC) + timedelta(minutes=5)).timestamp())
    claims = (
        base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
    )
    token = f"header.{claims}.signature"

    assert FFClient.access_token_expires_at(token) == datetime.fromtimestamp(
        exp, tz=UTC
    )
    assert FFClient.access_token_expires_at("not-a-jwt") is None


def test_bank_field_errors_are_readable():
    response = httpx.Response(400, json={"partner": ["Обязательное поле."]})
    with pytest.raises(FFClientError) as exc_info:
        FFClient._parse_response(response)
    assert exc_info.value.detail == "Банк отклонил заявку: partner: Обязательное поле."
