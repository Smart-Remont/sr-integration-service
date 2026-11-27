import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.features.installment.oa_service import InstallmentOAService
from src.features.installment.repo import InstallmentRepository

_STATS = {
    "providers": [
        {
            "provider_code": "BIF",
            "total_count": 2,
            "total_amount": 2177790.00,
            "by_status": [{"status": "ISSUED", "count": 1, "amount": 900000.00}],
            "issued_count": 1,
            "issued_amount": 900000.00,
            "conversion": 0.5,
            "refunded_count": 0,
            "refunded_amount": 0,
        }
    ],
    "total": {
        "total_count": 2,
        "total_amount": 2177790.00,
        "by_status": [],
        "issued_count": 1,
        "issued_amount": 900000.00,
        "conversion": 0.5,
        "refunded_count": 0,
        "refunded_amount": 0,
    },
}


@pytest.mark.asyncio
async def test_stats_amounts_have_two_decimals():
    repo = InstallmentRepository(connection=MagicMock())
    # As the database answers: jsonb text with numeric(18,2) sums.
    repo.call_sp = AsyncMock(
        return_value=[
            {"installment__oa_stats": json.dumps(_STATS).replace("2177790.0", "2177790.00")}
        ]
    )

    stats = await InstallmentOAService(repo).stats(provider_code=None, date_from=None, date_to=None)
    body = stats.model_dump(mode="json")

    assert body["total"]["total_amount"] == "2177790.00"
    assert body["total"]["refunded_amount"] == "0.00"
    assert body["providers"][0]["by_status"][0]["amount"] == "900000.00"
    assert body["providers"][0]["conversion"] == 0.5


@pytest.mark.asyncio
async def test_list_items_carry_borrower_iin():
    repo = MagicMock()
    row = {
        "id": 15,
        "client_request_id": 1,
        "provider_code": "BIF",
        "status": "ISSUED",
        "iin": "900101300123",
        "client_fio": "Иванов",
        "total_count": 1,
        "created_at": "2026-11-24T10:00:00+00:00",
        "updated_at": "2026-11-24T10:00:00+00:00",
    }
    repo.list_applications_for_oa = AsyncMock(return_value=[row])

    answer = await InstallmentOAService(repo).list_applications(
        provider_code=None,
        status=None,
        date_from=None,
        date_to=None,
        client_request_id=None,
        search=None,
        limit=50,
        offset=0,
    )

    assert answer.items[0].iin == "900101300123"
