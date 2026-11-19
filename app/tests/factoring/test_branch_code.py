from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.features.factoring.ff.client import FactoringClient, FactoringClientError
from src.features.factoring.ff.service import FactoringService

IIN = "040516551071"
PHONE = "+77057238447"


def _service() -> FactoringService:
    client = MagicMock()
    client.get_branch_code = AsyncMock(return_value={"branchCode": "430000"})
    service = FactoringService(repository=MagicMock(), client=client, app_env="test")
    service._ensure_valid_token = AsyncMock(return_value="token")  # type: ignore[method-assign]
    service._authenticate_and_store_token = AsyncMock(return_value="new-token")  # type: ignore[method-assign]
    service._log_event = AsyncMock()  # type: ignore[method-assign]
    return service


def _provider() -> MagicMock:
    provider = MagicMock()
    provider.config = {}
    provider.base_url = "https://bank.test/"
    return provider


async def _resolve(service: FactoringService, requested: str | None = "300000") -> str:
    return await service._resolve_branch_code(
        _provider(), iin=IIN, phone=PHONE, requested=requested, client_request_id=1
    )


async def test_bank_branch_wins_over_crm():
    service = _service()
    assert await _resolve(service) == "430000"
    assert service._log_event.await_args.args[0] == "BRANCH_CODE_RESOLVED"
    assert service._log_event.await_args.kwargs["payload"]["requested"] == "300000"


async def test_journal_has_no_personal_data():
    service = _service()
    await _resolve(service)
    text = str(service._log_event.await_args)
    assert IIN not in text
    assert PHONE not in text


@pytest.mark.parametrize(
    ("status_code", "detail"),
    [(400, '{"message":"error","description":"Colvir"}'), (500, "Server Error"), (502, "timeout")],
)
async def test_bank_failure_keeps_crm_branch(status_code: int, detail: str):
    service = _service()
    service.client.get_branch_code.side_effect = FactoringClientError(status_code, detail)
    assert await _resolve(service) == "300000"
    assert service._log_event.await_args.args[0] == "BRANCH_CODE_FALLBACK"


@pytest.mark.parametrize(
    "answer", [{}, {"branchCode": ""}, {"branchCode": "ALMATY"}, {"branchCode": 200000}]
)
async def test_unexpected_answer_keeps_crm_branch(answer: dict):
    service = _service()
    service.client.get_branch_code.return_value = answer
    assert await _resolve(service) == "300000"
    assert service._log_event.await_args.args[0] == "BRANCH_CODE_FALLBACK"


async def test_no_crm_branch_falls_back_to_default():
    service = _service()
    service.client.get_branch_code.side_effect = FactoringClientError(500, "x")
    assert await _resolve(service, requested=None) == "200000"


async def test_expired_token_is_refreshed_once():
    service = _service()
    service.client.get_branch_code.side_effect = [
        FactoringClientError(401, "expired"),
        {"branchCode": "200000"},
    ]
    assert await _resolve(service) == "200000"
    assert service.client.get_branch_code.await_args.kwargs["access_token"] == "new-token"


async def test_client_sends_params_and_masks_them_in_log():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"branchCode": "200000"})

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    with (
        patch(
            "src.features.factoring.ff.client.httpx.AsyncClient",
            side_effect=lambda **kwargs: real_client(transport=transport, **kwargs),
        ),
        patch("src.features.factoring.ff.client.logger") as logger,
    ):
        answer = await FactoringClient().get_branch_code(
            base_url="https://bank.test/", access_token="t", iin=IIN, phone=PHONE
        )
    assert answer == {"branchCode": "200000"}
    assert f"iin={IIN}" in captured["url"]
    assert "phoneNumber=%2B77057238447" in captured["url"]
    logged = str(logger.info.call_args_list)
    assert IIN not in logged
    assert "77057238447" not in logged
