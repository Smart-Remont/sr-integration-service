from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from src.features.factoring.ff.client import FactoringClientError
from src.features.factoring.ff.repo import FactoringProvider
from src.features.factoring.ff.service import FactoringService
from src.features.factoring.schemas import FactoringApplicationResponse, SubmitFactoringApplicationRequest

CONFIG = {
    "prescoring_required": True,
    "prescoring_base_url": "http://prescoring.test",
    "prescoring_credentials": {"FACTORING_SO": {"user": "u", "password": "p"}},
    "partner_by_company_id": {"9": "FACTORING_SO"},
    "default_product_id": "FACTORING_SR",
    "channel": "CH",
    "hook_url": "https://hook",
    "success_url": "https://ok",
    "failure_url": "https://fail",
}


def _application(*, checked_ago: timedelta, **overrides) -> FactoringApplicationResponse:
    defaults = dict(
        id=12,
        client_request_id=3214842,
        provider_code="FF_FACTORING",
        credit_contract="FCT26-200000-3214842",
        status="WAITING_SIGN",
        principal=Decimal("1000000"),
        period=6,
        partner="FACTORING_SO",
        created_by=2543,
        request_payload={"iin": "040516551071", "mobile_phone": "+77011234567"},
        print_forms=[{"name": "application", "signed": True, "file_token": "t"}],
        prescoring_status="APPROVED",
        prescoring_score=Decimal("0.9"),
        prescoring_max_limit=Decimal("3000000"),
        prescoring_checked_at=datetime.now(UTC) - checked_ago,
    )
    defaults.update(overrides)
    return FactoringApplicationResponse(**defaults)


def _service(application: FactoringApplicationResponse, prescoring: dict | Exception) -> FactoringService:
    repository = MagicMock()
    repository.get_application_by_id = AsyncMock(return_value=application)
    repository.get_provider_by_code = AsyncMock(
        return_value=FactoringProvider(id=1, code="FF_FACTORING", base_url="https://bank", config=CONFIG)
    )
    repository.get_provider_webhook_credentials = AsyncMock(return_value=MagicMock())
    repository.update_application_prescoring = AsyncMock()
    repository.reject_application_on_prescoring_committed = AsyncMock()

    client = MagicMock()
    if isinstance(prescoring, Exception):
        client.prescoring = AsyncMock(side_effect=prescoring)
    else:
        client.prescoring = AsyncMock(return_value=prescoring)

    service = FactoringService(repository=repository, client=client, app_env="test")
    service._log_event = AsyncMock()
    service._poll_print_form_signatures = AsyncMock(return_value=[])
    service._apply_with_reauth = AsyncMock(
        side_effect=HTTPException(status_code=422, detail='HTTP 400: {"description":"bad"}')
    )
    return service


def _logged(service: FactoringService, event_type: str) -> list:
    return [c for c in service._log_event.await_args_list if c.args[0] == event_type]


async def _submit(service: FactoringService) -> HTTPException:
    with pytest.raises(HTTPException) as exc:
        await service.submit_application(12, SubmitFactoringApplicationRequest())
    return exc.value


@pytest.mark.asyncio
async def test_fresh_prescoring_is_not_repeated() -> None:
    service = _service(_application(checked_ago=timedelta(minutes=10)), {"status": "APPROVED"})

    await _submit(service)

    service.client.prescoring.assert_not_awaited()
    service.repository.update_application_prescoring.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_prescoring_is_repeated_instead_of_blocking_submit() -> None:
    stale = _application(checked_ago=timedelta(hours=5))
    fresh = _application(checked_ago=timedelta(seconds=1))
    service = _service(stale, {"status": "APPROVED", "score": 0.8, "max_limit": 2500000})
    service.repository.get_application_by_id.side_effect = [stale, fresh, fresh]

    exc = await _submit(service)

    # Дошли до банка: ошибка — от apply, а не «Прескоринг устарел».
    assert exc.detail == 'HTTP 400: {"description":"bad"}'
    service.client.prescoring.assert_awaited_once()
    kwargs = service.repository.update_application_prescoring.await_args.kwargs
    assert kwargs["prescoring_status"] == "APPROVED"
    assert kwargs["prescoring_max_limit"] == Decimal("2500000")
    service.repository.reject_application_on_prescoring_committed.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_prescoring_rejected_closes_application() -> None:
    service = _service(
        _application(checked_ago=timedelta(hours=5)),
        {"status": "REJECTED", "message": "Отказ по скорингу"},
    )

    exc = await _submit(service)

    assert exc.status_code == 422
    assert "Отказ по скорингу" in exc.detail
    assert "Заявка закрыта" in exc.detail
    kwargs = service.repository.reject_application_on_prescoring_committed.await_args.kwargs
    assert kwargs["application_id"] == 12
    assert kwargs["prescoring_status"] == "REJECTED"
    failed = _logged(service, "FF_APPLY_FAILED")[0].kwargs
    assert failed["committed"] is True
    assert failed["payload"]["stage"] == "prescoring_on_submit"
    service._poll_print_form_signatures.assert_not_awaited()
    service._apply_with_reauth.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_prescoring_over_new_limit_closes_application() -> None:
    service = _service(
        _application(checked_ago=timedelta(hours=5)),
        {"status": "APPROVED", "max_limit": 500000},
    )

    exc = await _submit(service)

    assert "превышает лимит прескоринга" in exc.detail
    service.repository.reject_application_on_prescoring_committed.assert_awaited_once()


@pytest.mark.asyncio
async def test_prescoring_unavailable_keeps_application_open() -> None:
    service = _service(
        _application(checked_ago=timedelta(hours=5)),
        FactoringClientError(status_code=502, detail="timeout"),
    )

    exc = await _submit(service)

    assert exc.status_code == 503
    service.repository.reject_application_on_prescoring_committed.assert_not_awaited()
    assert _logged(service, "PRESCORING_FAILED")[0].kwargs["committed"] is True


@pytest.mark.asyncio
async def test_bank_apply_failure_is_logged_on_committed_connection() -> None:
    service = _service(_application(checked_ago=timedelta(minutes=10)), {"status": "APPROVED"})

    await _submit(service)

    assert _logged(service, "FF_APPLY_REQUEST")[0].kwargs["committed"] is True
    assert _logged(service, "FF_APPLY_FAILED")[0].kwargs["committed"] is True


@pytest.mark.asyncio
async def test_prescoring_rejection_on_prepare_is_logged_on_committed_connection() -> None:
    service = _service(_application(checked_ago=timedelta(hours=5)), {"status": "REJECTED"})

    await _submit(service)

    for event_type in ("PRESCORING_REQUEST", "PRESCORING_RESULT"):
        assert _logged(service, event_type)[0].kwargs["committed"] is True
