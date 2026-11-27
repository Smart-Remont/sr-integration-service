import base64
import json
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from src.features.installment.bif.webhook import (
    BIFApplicationNotFoundError,
    BIFWebhookService,
)
from src.features.installment.deps import get_bif_webhook_service
from src.main import app
from tests.installment.bif_fakes import (
    HOOK_PASSWORD,
    HOOK_USER,
    application,
    events,
    repository,
)

ROUTE = "/api/v1/installment/ff/webhook/bif"


def _auth(user: str = HOOK_USER, password: str = HOOK_PASSWORD) -> str:
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


def _body(**fields) -> bytes:
    return json.dumps({"appId": "1042", "status": "APPROVED", **fields}).encode()


@pytest.mark.asyncio
async def test_approved_stores_conditions_as_sent_and_comment():
    repo = repository()
    svc = BIFWebhookService(repo)
    conditions = {"amount": 900000, "termMonths": 12, "rate": 24.9, "monthlyPayment": 95000}

    answer = await svc.handle_status(
        _body(approvedConditions=conditions, comment="ок"), authorization_header=_auth()
    )

    assert answer.ok is True
    kwargs = repo.update_application_from_bif.await_args.kwargs
    assert kwargs["status"] == "APPROVED"
    assert kwargs["approved_params"] == {
        **conditions,
        "principal": 900000,
        "period": 12,
        "comment": "ок",
    }
    assert "STATUS_UPDATED" in events(repo)
    repo.apply_application_to_deal.assert_not_awaited()


@pytest.mark.asyncio
async def test_repeat_of_same_status_changes_nothing():
    repo = repository()
    repo.update_application_from_bif = AsyncMock(
        return_value={"application_id": 15, "changed": False, "status": "APPROVED"}
    )
    svc = BIFWebhookService(repo)

    answer = await svc.handle_status(_body(), authorization_header=_auth())

    assert answer.ok is True
    assert "HOOK_DUPLICATE" in events(repo)
    assert "STATUS_UPDATED" not in events(repo)


@pytest.mark.asyncio
async def test_issued_puts_credit_into_deal():
    repo = repository()
    repo.update_application_from_bif = AsyncMock(
        return_value={"application_id": 15, "changed": True, "status": "ISSUED"}
    )
    repo.get_application_by_id = AsyncMock(return_value=application(status="ISSUED"))
    svc = BIFWebhookService(repo)

    await svc.handle_status(_body(status="ISSUED"), authorization_header=_auth())

    repo.apply_application_to_deal.assert_awaited_once_with(application_id=15, created_by=7)
    assert "AUTO_APPLY" in events(repo)


@pytest.mark.asyncio
async def test_deal_refusal_does_not_fail_the_hook():
    repo = repository()
    repo.update_application_from_bif = AsyncMock(
        return_value={"application_id": 15, "changed": True, "status": "ISSUED"}
    )
    repo.get_application_by_id = AsyncMock(return_value=application(status="ISSUED"))
    repo.apply_application_to_deal = AsyncMock(side_effect=RuntimeError("deal is signed"))
    svc = BIFWebhookService(repo)

    answer = await svc.handle_status(_body(status="ISSUED"), authorization_header=_auth())

    assert answer.ok is True
    assert "AUTO_APPLY_FAILED" in events(repo)


@pytest.mark.asyncio
async def test_unknown_app_id_raises_not_found():
    repo = repository()
    repo.find_application_by_uuid = AsyncMock(return_value=None)
    svc = BIFWebhookService(repo)

    with pytest.raises(BIFApplicationNotFoundError):
        await svc.handle_status(_body(), authorization_header=_auth())
    repo.update_application_from_bif.assert_not_awaited()
    assert "WEBHOOK_ORPHAN" in events(repo)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        json.dumps({"status": "APPROVED"}).encode(),
        json.dumps({"appId": "1042", "status": "SIGNED"}).encode(),
    ],
)
async def test_bad_body_is_400(body):
    repo = repository()
    svc = BIFWebhookService(repo)

    with pytest.raises(HTTPException) as exc:
        await svc.handle_status(body, authorization_header=_auth())

    assert exc.value.status_code == 400
    repo.update_application_from_bif.assert_not_awaited()


@pytest.mark.asyncio
async def test_wrong_login_is_401_before_reading_body():
    repo = repository()
    svc = BIFWebhookService(repo)

    with pytest.raises(HTTPException) as exc:
        await svc.handle_status(b"not json", authorization_header=_auth(password="wrong"))

    assert exc.value.status_code == 401
    assert "WEBHOOK_AUTH_FAILED" in events(repo)


@pytest.mark.asyncio
async def test_no_login_configured_is_503():
    repo = repository()
    repo.get_provider_webhook_credentials = AsyncMock(return_value=None)
    svc = BIFWebhookService(repo)

    with pytest.raises(HTTPException) as exc:
        await svc.handle_status(_body(), authorization_header=_auth())

    assert exc.value.status_code == 503


@pytest.fixture
def client_with(request):
    repo = repository()
    for name, value in getattr(request, "param", {}).items():
        setattr(repo, name, value)
    app.dependency_overrides[get_bif_webhook_service] = lambda: BIFWebhookService(repo)
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize(
    "client_with", [{"find_application_by_uuid": AsyncMock(return_value=None)}], indirect=True
)
def test_route_answers_app_not_found_as_the_bank_expects(client_with):
    response = client_with.post(ROUTE, content=_body(), headers={"Authorization": _auth()})

    assert response.status_code == 404
    assert response.json() == {"code": "APP_NOT_FOUND"}


def test_route_answers_400_not_422_on_bad_body(client_with):
    response = client_with.post(ROUTE, content=b"{", headers={"Authorization": _auth()})

    assert response.status_code == 400


def test_route_answers_200(client_with):
    response = client_with.post(ROUTE, content=_body(), headers={"Authorization": _auth()})

    assert response.status_code == 200
    assert response.json() == {"ok": True, "status": True}


@pytest.mark.asyncio
async def test_status_after_final_one_is_ignored():
    repo = repository()
    repo.update_application_from_bif = AsyncMock(
        return_value={"application_id": 15, "changed": False, "status": "ISSUED"}
    )
    svc = BIFWebhookService(repo)

    answer = await svc.handle_status(_body(status="CANCELLED"), authorization_header=_auth())

    assert answer.ok is True
    assert "HOOK_IGNORED" in events(repo)


@pytest.mark.asyncio
async def test_unknown_app_id_while_orders_await_bank_number_is_503():
    repo = repository()
    repo.find_application_by_uuid = AsyncMock(return_value=None)
    repo.count_applications_awaiting_bank_number = AsyncMock(return_value=1)
    svc = BIFWebhookService(repo)

    with pytest.raises(HTTPException) as exc:
        await svc.handle_status(_body(), authorization_header=_auth())

    assert exc.value.status_code == 503
    assert "WEBHOOK_EARLY" in events(repo)


@pytest.mark.asyncio
async def test_numeric_app_id_is_taken_as_text():
    repo = repository()
    svc = BIFWebhookService(repo)

    await svc.handle_status(
        json.dumps({"appId": 1042, "status": "APPROVED"}).encode(), authorization_header=_auth()
    )

    assert repo.find_application_by_uuid.await_args.args == ("BIF", "1042")


@pytest.mark.asyncio
async def test_late_issue_with_other_application_on_deal_is_not_applied():
    repo = repository()
    repo.update_application_from_bif = AsyncMock(
        return_value={
            "application_id": 15,
            "changed": True,
            "status": "ISSUED",
            "after_expired": True,
            "other_application_id": 31,
        }
    )
    repo.get_application_by_id = AsyncMock(return_value=application(status="ISSUED"))
    svc = BIFWebhookService(repo)

    await svc.handle_status(_body(status="ISSUED"), authorization_header=_auth())

    repo.apply_application_to_deal.assert_not_awaited()
    assert {"LATE_ISSUED_AFTER_EXPIRED", "AUTO_APPLY_SKIPPED"} <= set(events(repo))


@pytest.mark.asyncio
async def test_late_issue_without_other_application_is_applied():
    repo = repository()
    repo.update_application_from_bif = AsyncMock(
        return_value={
            "application_id": 15,
            "changed": True,
            "status": "ISSUED",
            "after_expired": True,
            "other_application_id": None,
        }
    )
    repo.get_application_by_id = AsyncMock(return_value=application(status="ISSUED"))
    svc = BIFWebhookService(repo)

    await svc.handle_status(_body(status="ISSUED"), authorization_header=_auth())

    repo.apply_application_to_deal.assert_awaited_once()
    assert "LATE_ISSUED_AFTER_EXPIRED" in events(repo)
