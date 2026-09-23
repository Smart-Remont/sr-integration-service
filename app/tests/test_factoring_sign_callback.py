from unittest.mock import AsyncMock, MagicMock

import pytest

from src.features.factoring.ff.service import FactoringService
from src.features.factoring.schemas import FactoringApplicationResponse


def _application(*, iin: str | None = "040516551071") -> FactoringApplicationResponse:
    return FactoringApplicationResponse(
        id=38,
        client_request_id=3041810,
        provider_code="FF_FACTORING",
        credit_contract="FCT26-300000-3041810",
        status="WAITING_SIGN",
        request_payload={"iin": iin} if iin else {},
    )


def _service(application: FactoringApplicationResponse | None) -> FactoringService:
    repository = MagicMock()
    repository.get_application_by_sign_process_id = AsyncMock(return_value=application)
    return FactoringService(repository=repository, client=MagicMock(), app_env="test")


@pytest.mark.asyncio
async def test_sign_callback_rejects_iin_mismatch():
    dn_name = "CN=Other, SERIALNUMBER=IIN911019401457"
    svc = _service(_application())

    result = await svc.handle_sign_callback(
        {
            "status": "SUCCESS",
            "sign_process_id": "pid-1",
            "dn_name": dn_name,
        }
    )

    assert result == {
        "status": False,
        "error": (
            "ИИН подписанта не соответствует заявке "
            f"(ожидается 040516551071, DN: {dn_name})"
        ),
    }


@pytest.mark.asyncio
async def test_sign_callback_acks_matching_iin():
    svc = _service(_application())

    result = await svc.handle_sign_callback(
        {
            "status": "SUCCESS",
            "sign_process_id": "pid-1",
            "dn_name": "CN=Client, serialNumber = IIN040516551071",
        }
    )

    assert result == {"status": True, "error": None}


@pytest.mark.asyncio
async def test_sign_callback_rejects_missing_dn_name():
    svc = _service(_application())

    result = await svc.handle_sign_callback(
        {"status": "SUCCESS", "sign_process_id": "pid-1"}
    )

    assert result == {"status": False, "error": "Данные подписавшего не были получены"}


@pytest.mark.asyncio
async def test_sign_callback_ignores_non_success():
    svc = _service(_application())

    result = await svc.handle_sign_callback(
        {
            "status": "EXPIRED",
            "sign_process_id": "pid-1",
            "dn_name": "CN=Other, SERIALNUMBER=IIN911019401457",
        }
    )

    assert result == {"status": True, "error": None}
    svc.repository.get_application_by_sign_process_id.assert_not_called()


def test_extract_iin_from_dn_ignores_bin():
    dn_name = "CN=TOO, SERIALNUMBER=BIN123456789012, SERIALNUMBER=IIN040516551071"
    assert FactoringService._extract_iin_from_dn(dn_name) == "040516551071"
