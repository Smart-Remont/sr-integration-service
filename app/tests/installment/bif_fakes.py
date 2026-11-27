"""A BI Finance provider, a mocked repository and stored applications for the BIF tests."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import bcrypt

from src.features.installment.repo import (
    ApplicationBorrower,
    InstallmentProvider,
    WebhookCredential,
)
from src.features.installment.schemas import (
    CreateBIFApplicationRequest,
    InstallmentApplicationResponse,
)

HOOK_USER = "bif-hook"
HOOK_PASSWORD = "bif-hook-secret"
HOOK_CREDENTIAL = WebhookCredential(
    username=HOOK_USER,
    password_hash=bcrypt.hashpw(HOOK_PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode(),
)

PROVIDER = InstallmentProvider(
    id=9,
    code="BIF",
    base_url="https://gateway.bif.example",
    config={
        "product_code": "bezzalogovyy-kredit",
        "dealer_id": "111111111111",
        "repayment_type": "0",
        "scheme_def_payment": "0",
        "is_deferred_principal": False,
        "expire_working_days": 3,
    },
)


def bif_request(**overrides: Any) -> CreateBIFApplicationRequest:
    values: dict[str, Any] = {
        "client_request_id": 3041810,
        "provider_code": "BIF",
        "bank_id": 101,
        "iin": "900101300123",
        "mobile_phone": "+7 (701) 000-00-00",
        "principal": 1000000,
        "period": 12,
        "initial_fee": Decimal(0),
        "payment_day": 10,
        "created_by": 7,
    }
    values.update(overrides)
    return CreateBIFApplicationRequest(**values)


def application(**overrides: Any) -> InstallmentApplicationResponse:
    now = datetime.now(UTC)
    values: dict[str, Any] = {
        "id": 15,
        "client_request_id": 3041810,
        "provider_code": "BIF",
        "uuid": "1042",
        "reference_id": "SR-15",
        "status": "IN_PROGRESS",
        "principal": Decimal(1000000),
        "period": 12,
        "initial_fee": Decimal(0),
        "payment_day": 10,
        "expires_at": now + timedelta(days=3),
        "bank_id": 101,
        "created_by": 7,
        "created_at": now,
        "updated_at": now,
    }
    values.update(overrides)
    return InstallmentApplicationResponse(**values)


def repository() -> MagicMock:
    repo = MagicMock()
    repo.get_provider_by_code = AsyncMock(return_value=PROVIDER)
    repo.get_provider_webhook_credentials = AsyncMock(return_value=HOOK_CREDENTIAL)
    repo.get_deal_client_request_state = AsyncMock(
        return_value={"status_code": "CONFIRM", "crm_deal_id": 1}
    )
    repo.get_deal_seller = AsyncMock(
        return_value={"company_id": 8, "bin": "222222222222", "name": "TOO Azure", "iban": "KZ01"}
    )
    repo.get_applications_by_client_request = AsyncMock(return_value=[])
    repo.insert_application = AsyncMock(return_value=15)
    repo.update_application_after_apply = AsyncMock()
    repo.mark_apply_failed = AsyncMock()
    repo.mark_apply_unknown = AsyncMock()
    repo.insert_event_log = AsyncMock()
    repo.get_token = AsyncMock(return_value=None)
    repo.upsert_token = AsyncMock()
    repo.get_active_credentials = AsyncMock(
        return_value=MagicMock(username="client-id", password="client-secret")
    )
    repo.get_application_by_id = AsyncMock(return_value=application())
    repo.find_application_by_uuid = AsyncMock(return_value=MagicMock(id=15, status="IN_PROGRESS"))
    repo.update_application_from_bif = AsyncMock(
        return_value={"application_id": 15, "changed": True, "status": "APPROVED"}
    )
    repo.apply_application_to_deal = AsyncMock(return_value=555)
    repo.refund_manual = AsyncMock(return_value={"application_id": 15})
    repo.lock_token_renewal = AsyncMock()
    repo.count_applications_awaiting_bank_number = AsyncMock(return_value=0)
    repo.get_application_borrower = AsyncMock(
        return_value=ApplicationBorrower(
            iin="900101300123", mobile_phone="77010000000", bank_id=101
        )
    )
    repo.expire_applications = AsyncMock(return_value={"expired": 2, "ids": [3, 4]})

    async def _committed(action):
        return await action(repo)

    repo.committed = AsyncMock(side_effect=_committed)
    return repo


def events(repo: MagicMock) -> list[str]:
    return [call.kwargs["event_type"] for call in repo.insert_event_log.await_args_list]
