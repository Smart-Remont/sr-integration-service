from unittest.mock import AsyncMock, MagicMock

import pytest

from src.exceptions import StoredProcedureError
from src.features.factoring.ff.service import FactoringService
from src.features.factoring.schemas import FactoringApplicationResponse


def _application(**overrides) -> FactoringApplicationResponse:
    defaults = dict(
        id=38,
        client_request_id=3041810,
        provider_code="FF_FACTORING",
        credit_contract="FCT26-300000-3041810",
        status="ISSUED",
        created_by=42,
        client_request_credit_detail_id=None,
    )
    defaults.update(overrides)
    return FactoringApplicationResponse(**defaults)


def _service(application: FactoringApplicationResponse | None) -> FactoringService:
    repository = MagicMock()
    repository.get_application_by_id = AsyncMock(return_value=application)
    repository.apply_application_to_deal = AsyncMock(return_value=777)
    repository.insert_event_log = AsyncMock()
    return FactoringService(repository=repository, client=MagicMock(), app_env="test")


@pytest.mark.asyncio
async def test_auto_apply_creates_credit_detail_once():
    svc = _service(_application())

    await svc._try_auto_apply_on_issued(38)

    svc.repository.apply_application_to_deal.assert_awaited_once_with(
        application_id=38, created_by=42
    )


@pytest.mark.asyncio
async def test_auto_apply_skips_when_already_applied():
    svc = _service(_application(client_request_credit_detail_id=5))

    await svc._try_auto_apply_on_issued(38)

    svc.repository.apply_application_to_deal.assert_not_awaited()


@pytest.mark.asyncio
async def test_auto_apply_skips_when_not_issued():
    svc = _service(_application(status="WAITING_SIGN"))

    await svc._try_auto_apply_on_issued(38)

    svc.repository.apply_application_to_deal.assert_not_awaited()


@pytest.mark.asyncio
async def test_auto_apply_logs_failure_without_raising():
    svc = _service(_application())
    svc.repository.apply_application_to_deal = AsyncMock(
        side_effect=StoredProcedureError("У заявки не указан bank_id [installment]")
    )

    await svc._try_auto_apply_on_issued(38)  # must not raise

    events = [
        call.kwargs["event_type"] for call in svc.repository.insert_event_log.await_args_list
    ]
    assert "AUTO_APPLY_FAILED" in events


@pytest.mark.asyncio
async def test_auto_apply_skips_when_created_by_missing():
    svc = _service(_application(created_by=None))

    await svc._try_auto_apply_on_issued(38)

    svc.repository.apply_application_to_deal.assert_not_awaited()
