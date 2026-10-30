"""POST /api/v1/factoring/ff/applications/{id}/cancel: cancel before the bank.

Allowed only from NEW / WAITING_SIGN without a bank uuid; the result is CANCELLED,
an event CANCELLED goes to the committed event log, MyNCA is never called, and a
CANCELLED application no longer blocks a new one on the deal. The repository is a
mock; the SQL side is checked on dev_prod (sql/factoring/35_cancel_before_bank.sql).
"""

import base64
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from src.config import factoring_auth_config
from src.exceptions import StoredProcedureError
from src.features.factoring.deps import get_factoring_service
from src.features.factoring.ff.service import RETRYABLE_APPLICATION_STATUSES, FactoringService
from src.features.factoring.schemas import (
    CancelFactoringApplicationRequest,
    FactoringApplicationResponse,
)
from src.integration_context.constants import SCOPE_FACTORING
from src.integration_context.deps import get_integration_context
from src.integration_context.token import IntegrationContext
from src.main import app

APPLICATION_ID = 13
PASSWORD = "factoring-secret"
URL = f"/api/v1/factoring/ff/applications/{APPLICATION_ID}/cancel"


def _application(status: str = "WAITING_SIGN", uuid: str | None = None):
    return FactoringApplicationResponse(
        id=APPLICATION_ID,
        client_request_id=3211717,
        provider_code="FF_FACTORING",
        credit_contract="FCT26-200000-3211717",
        status=status,
        uuid=uuid,
        print_forms=[{"name": "application", "sign_process_id": "pid-1", "signed": False}],
        request_payload={"iin": "040516551071", "mobile_phone": "+77001234567"},
    )


def _service(*states: FactoringApplicationResponse) -> FactoringService:
    repository = MagicMock()
    repository.get_application_by_id = AsyncMock(side_effect=list(states))
    repository.cancel_application = AsyncMock()
    repository.insert_event_log = AsyncMock()
    repository.insert_event_log_committed = AsyncMock()
    mynca = MagicMock()
    mynca.sign_status = AsyncMock()
    return FactoringService(repository=repository, client=MagicMock(), app_env="test", mynca=mynca)


def _request(reason: str | None = "Клиент передумал") -> CancelFactoringApplicationRequest:
    return CancelFactoringApplicationRequest(cancelled_by=42, reason=reason)


@pytest.mark.parametrize("status", ["NEW", "WAITING_SIGN"])
async def test_cancel_before_bank_returns_cancelled_and_logs(status: str) -> None:
    service = _service(_application(status), _application("CANCELLED"))

    result = await service.cancel_application(APPLICATION_ID, _request())

    assert result.status == "CANCELLED"
    service.repository.cancel_application.assert_awaited_once_with(
        application_id=APPLICATION_ID, cancelled_by=42
    )
    service.repository.insert_event_log_committed.assert_awaited_once_with(
        factoring_id=APPLICATION_ID,
        event_type="CANCELLED",
        payload={"cancelled_by": 42, "reason": "Клиент передумал", "previous_status": status},
        source="FACTORING",
    )
    service.repository.insert_event_log.assert_not_awaited()
    service.mynca.sign_status.assert_not_awaited()


async def test_cancel_response_hides_print_form_secrets() -> None:
    cancelled = _application("CANCELLED")
    cancelled.print_forms = [{"name": "application", "file_token": "secret", "signed": False}]
    service = _service(_application(), cancelled)

    result = await service.cancel_application(APPLICATION_ID, _request(reason=None))

    assert "file_token" not in result.print_forms[0]
    payload = service.repository.insert_event_log_committed.await_args.kwargs["payload"]
    assert payload["reason"] is None


@pytest.mark.parametrize(
    ("application", "detail"),
    [
        (
            _application("WAITING_SIGN", uuid="fc58a802"),
            "Заявка уже отправлена в банк — отменить нельзя",
        ),
        (_application("IN_PROGRESS", uuid="fc58a802"), "Заявка уже отправлена в банк"),
        (_application("APPLY_FAILED"), "Заявку в статусе APPLY_FAILED отменить нельзя"),
        (_application("REJECTED"), "Заявку в статусе REJECTED отменить нельзя"),
        (_application("CANCELLED"), "Заявка уже отменена"),
    ],
)
async def test_cancel_refused_after_bank_or_closed(
    application: FactoringApplicationResponse, detail: str
) -> None:
    service = _service(application)

    with pytest.raises(HTTPException) as caught:
        await service.cancel_application(APPLICATION_ID, _request())

    assert caught.value.status_code == 409
    assert detail in caught.value.detail
    service.repository.cancel_application.assert_not_awaited()
    service.repository.insert_event_log_committed.assert_not_awaited()


async def test_cancel_race_in_database_becomes_409_without_log() -> None:
    service = _service(_application())
    service.repository.cancel_application.side_effect = StoredProcedureError(
        "Заявка уже отправлена в банк — отменить нельзя [factoring]"
    )

    with pytest.raises(HTTPException) as caught:
        await service.cancel_application(APPLICATION_ID, _request())

    assert caught.value.status_code == 409
    assert caught.value.detail.startswith("Заявка уже отправлена в банк")
    service.repository.insert_event_log_committed.assert_not_awaited()


async def test_cancelled_application_cannot_be_submitted() -> None:
    service = _service(_application("CANCELLED"))
    service.repository.get_provider_by_code = AsyncMock(return_value=MagicMock(id=1, config={}))
    service.repository.get_provider_webhook_credentials = AsyncMock(return_value=MagicMock())

    with pytest.raises(HTTPException) as caught:
        await service.submit_application(APPLICATION_ID, MagicMock(is_knox=False))

    assert caught.value.status_code == 409
    service.mynca.sign_status.assert_not_awaited()


async def test_late_sign_callback_does_not_revive_cancelled_application() -> None:
    service = _service()
    repository = service.repository
    repository.get_application_by_sign_process_id = AsyncMock(
        return_value=_application("CANCELLED")
    )

    single = await service.handle_sign_callback({"status": "SUCCESS", "sign_process_id": "pid-1"})
    batch = await service.handle_sign_callback(
        {"status": "success", "documents": [{"sign_process_id": "pid-1"}]}
    )

    assert single == {"status": True, "error": None}
    assert batch["status"] == "success"
    # The callback only acknowledges: no status, print form or apply write.
    written = {name for name, *_ in repository.method_calls}
    assert written == {"get_application_by_sign_process_id"}


def test_cancelled_does_not_block_a_new_application() -> None:
    assert "CANCELLED" in RETRYABLE_APPLICATION_STATUSES


# --- HTTP: auth, body, employee from the integration context -----------------


def _auth() -> dict[str, str]:
    token = base64.b64encode(f"factoring:{PASSWORD}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest.fixture
def credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(factoring_auth_config, "username", "factoring")
    monkeypatch.setattr(factoring_auth_config, "password", PASSWORD)


@pytest.fixture
async def http(credentials: None) -> AsyncIterator[tuple[AsyncClient, FactoringService]]:
    service = _service(_application(), _application("CANCELLED"))
    app.dependency_overrides[get_factoring_service] = lambda: service
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, service
    finally:
        app.dependency_overrides.clear()


def _context(application_id: int, employee_id: int = 777) -> IntegrationContext:
    return IntegrationContext(
        employee_id=employee_id,
        scopes=(SCOPE_FACTORING,),
        ctx={SCOPE_FACTORING: {"app": application_id}},
        issuer="crm",
        audience="integrations-sr",
    )


async def test_http_cancel_requires_basic_auth(
    http: tuple[AsyncClient, FactoringService],
) -> None:
    client, service = http

    response = await client.post(URL, json={"cancelled_by": 42})

    assert response.status_code == 401
    service.repository.cancel_application.assert_not_awaited()


async def test_http_cancel_takes_employee_from_context(
    http: tuple[AsyncClient, FactoringService],
) -> None:
    client, service = http
    app.dependency_overrides[get_integration_context] = lambda: _context(APPLICATION_ID)

    response = await client.post(
        URL, json={"cancelled_by": 42, "reason": "Клиент передумал"}, headers=_auth()
    )

    assert response.status_code == 200
    assert response.json()["status"] == "CANCELLED"
    assert response.json()["id"] == APPLICATION_ID
    service.repository.cancel_application.assert_awaited_once_with(
        application_id=APPLICATION_ID, cancelled_by=777
    )


async def test_http_cancel_rejects_context_of_another_application(
    http: tuple[AsyncClient, FactoringService],
) -> None:
    client, service = http
    app.dependency_overrides[get_integration_context] = lambda: _context(APPLICATION_ID + 1)

    response = await client.post(URL, json={"cancelled_by": 42}, headers=_auth())

    assert response.status_code == 403
    service.repository.cancel_application.assert_not_awaited()


async def test_http_cancel_without_context_uses_body_employee(
    http: tuple[AsyncClient, FactoringService],
) -> None:
    client, service = http
    app.dependency_overrides[get_integration_context] = lambda: None

    response = await client.post(URL, json={"cancelled_by": 42}, headers=_auth())

    assert response.status_code == 200
    service.repository.cancel_application.assert_awaited_once_with(
        application_id=APPLICATION_ID, cancelled_by=42
    )
