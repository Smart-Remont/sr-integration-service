from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException

from src.features.factoring.ff.client import FactoringClientError
from src.features.factoring.ff.repo import CessionBatchItem, FactoringProvider
from src.features.factoring.ff.service import FactoringService
from src.features.factoring.schemas import SendCessionRequest

ALMATY = ZoneInfo("Asia/Almaty")

CONFIG = {
    "partner_by_company_id": {"9": "FACTORING_SO"},
    "framework_contract_by_company_id": {"9": {"num": "А0-17/704", "date": "2026-09-04"}},
}


def _yesterday() -> str:
    return (datetime.now(ALMATY).date() - timedelta(days=1)).isoformat()


def _base_number() -> str:
    day = _yesterday()
    return f"ЦЕС-{day[:4]}-{day[5:7]}{day[8:10]}-9"


def _item(item_id: int) -> CessionBatchItem:
    return CessionBatchItem(
        id=item_id,
        uuid=f"uuid-{item_id}",
        credit_contract=f"FCT-{item_id}",
        principal=Decimal("1200000"),
        status="ISSUED",
        company_id=9,
        period=12,
        issued_at=datetime.now(ALMATY) - timedelta(days=1),
    )


def _service(*, marked: int | None = None, used_numbers: list[str] | None = None) -> FactoringService:
    items = [_item(6)]
    repository = MagicMock()
    repository.list_cession_batch = AsyncMock(return_value=items)
    repository.get_provider_by_code = AsyncMock(
        return_value=FactoringProvider(id=1, code="FF_FACTORING", base_url="https://bank", config=CONFIG)
    )
    repository.get_cession_company = AsyncMock(return_value={"company_name_official": "ТОО «Smart Remont South»"})
    repository.list_cession_contract_numbers = AsyncMock(return_value=used_numbers or [])
    repository.mark_cession_sent_committed = AsyncMock(return_value=len(items) if marked is None else marked)
    repository.unmark_cession_sent_committed = AsyncMock(return_value=len(items))

    mynca = MagicMock()
    mynca.pkcs12_info = AsyncMock(return_value={"pubkey": "PUB", "subject": {}})
    mynca.cms_sign_save = AsyncMock(return_value={"sign_process_id": "sp-1", "group_id": "g-1"})
    mynca.download_cms = AsyncMock(return_value=b"CMS")

    client = MagicMock()
    client.send_cession = AsyncMock(return_value={"message": "ok"})

    service = FactoringService(repository=repository, client=client, mynca=mynca, app_env="test")
    service._get_cession_signer_key = AsyncMock(return_value=("KEY", "PWD"))
    service._build_cession_document = AsyncMock(return_value=b"%PDF")
    service._ensure_valid_token = AsyncMock(return_value="token")
    service._log_event = AsyncMock()
    return service


def _request(**overrides) -> SendCessionRequest:
    return SendCessionRequest(issue_date=_yesterday(), company_id=9, **overrides)


def _logged(service: FactoringService, event_type: str) -> list[dict]:
    return [c.kwargs["payload"] for c in service._log_event.await_args_list if c.args[0] == event_type]


@pytest.mark.asyncio
async def test_bank_rejection_unmarks_on_committed_connection() -> None:
    service = _service()
    service.client.send_cession.side_effect = FactoringClientError(
        status_code=400, detail='HTTP 400: {"description":"Не удалось отправить запрос в Freedom Front"}'
    )

    with pytest.raises(HTTPException) as exc:
        await service.send_daily_cession(_request())

    assert exc.value.status_code == 422
    service.repository.unmark_cession_sent_committed.assert_awaited_once_with([6], _base_number())
    assert _logged(service, "CESSION_FAILED")[0]["bank_rejected"] is True
    assert not _logged(service, "CESSION_SENT")


@pytest.mark.asyncio
async def test_unknown_outcome_keeps_mark_to_avoid_double_send() -> None:
    service = _service()
    service.client.send_cession.side_effect = FactoringClientError(
        status_code=502, detail="Freedom Factoring request failed: ReadTimeout"
    )

    with pytest.raises(HTTPException) as exc:
        await service.send_daily_cession(_request())

    assert exc.value.status_code == 502
    service.repository.unmark_cession_sent_committed.assert_not_awaited()
    assert _logged(service, "CESSION_FAILED")[0]["bank_rejected"] is False


@pytest.mark.asyncio
async def test_auth_failure_before_mark_sends_nothing() -> None:
    service = _service()
    service._ensure_valid_token.side_effect = FactoringClientError(status_code=400, detail="bad credentials")

    with pytest.raises(HTTPException):
        await service.send_daily_cession(_request())

    service.repository.mark_cession_sent_committed.assert_not_awaited()
    service.client.send_cession.assert_not_awaited()


@pytest.mark.asyncio
async def test_concurrent_send_gets_409_without_calling_bank() -> None:
    service = _service(marked=0)

    with pytest.raises(HTTPException) as exc:
        await service.send_daily_cession(_request())

    assert exc.value.status_code == 409
    service.client.send_cession.assert_not_awaited()
    service.repository.unmark_cession_sent_committed.assert_not_awaited()


@pytest.mark.asyncio
async def test_batch_changed_since_preview_gets_409() -> None:
    service = _service()

    with pytest.raises(HTTPException) as exc:
        await service.send_daily_cession(_request(expected_count=2))
    assert exc.value.status_code == 409

    with pytest.raises(HTTPException) as exc:
        await service.send_daily_cession(_request(expected_amount=Decimal("1000000")))
    assert exc.value.status_code == 409

    service._get_cession_signer_key.assert_not_awaited()


@pytest.mark.asyncio
async def test_success_logs_sent_and_keeps_mark() -> None:
    service = _service()

    response = await service.send_daily_cession(
        _request(expected_count=1, expected_amount=Decimal("1200000"))
    )

    assert response.batches[0].sent is True
    service.repository.unmark_cession_sent_committed.assert_not_awaited()
    assert _logged(service, "CESSION_SENT")


@pytest.mark.asyncio
async def test_contract_number_gets_suffix_when_base_already_used() -> None:
    base = _base_number()
    service = _service(used_numbers=[base, f"{base}-2"])

    response = await service.send_daily_cession(_request())

    assert response.batches[0].contract_number == f"{base}-3"


@pytest.mark.asyncio
async def test_sent_by_is_audited_but_never_sent_to_bank() -> None:
    service = _service()

    await service.send_daily_cession(_request(sent_by=2543))

    for event_type in ("CESSION_REQUEST", "CESSION_SENT"):
        assert _logged(service, event_type)[0]["sent_by"] == 2543
    assert "sent_by" not in service.client.send_cession.await_args.kwargs["payload"]


@pytest.mark.asyncio
async def test_sent_by_is_audited_on_bank_rejection() -> None:
    service = _service()
    service.client.send_cession.side_effect = FactoringClientError(status_code=400, detail="HTTP 400: no")

    with pytest.raises(HTTPException):
        await service.send_daily_cession(_request(sent_by=2543))

    assert _logged(service, "CESSION_FAILED")[0]["sent_by"] == 2543
