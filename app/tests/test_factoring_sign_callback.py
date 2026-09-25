from unittest.mock import AsyncMock, MagicMock

import pytest

from src.features.factoring.ff.mynca import MyncaClientError, parse_sign_batch_response
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


@pytest.mark.asyncio
async def test_batch_sign_callback_acks_both_documents():
    svc = _service(_application())

    result = await svc.handle_sign_callback(
        {
            "status": "success",
            "dn_name": "CN=Client, serialNumber = IIN040516551071",
            "documents": [
                {"sign_process_id": "pid-1"},
                {"sign_process_id": "pid-2"},
            ],
        }
    )

    assert result == {
        "status": "success",
        "documents": [
            {"sign_process_id": "pid-1", "status": "ok"},
            {"sign_process_id": "pid-2", "status": "ok"},
        ],
    }


@pytest.mark.asyncio
async def test_batch_sign_callback_rejects_iin_on_every_document():
    dn_name = "CN=Other, SERIALNUMBER=IIN911019401457"
    svc = _service(_application())

    result = await svc.handle_sign_callback(
        {
            "status": "success",
            "dn_name": dn_name,
            "documents": [
                {"sign_process_id": "pid-1"},
                {"sign_process_id": "pid-2"},
            ],
        }
    )

    error = (
        "ИИН подписанта не соответствует заявке "
        f"(ожидается 040516551071, DN: {dn_name})"
    )
    assert result == {
        "status": "success",
        "documents": [
            {"sign_process_id": "pid-1", "status": "error", "error": error},
            {"sign_process_id": "pid-2", "status": "error", "error": error},
        ],
    }


@pytest.mark.asyncio
async def test_batch_sign_callback_skips_iin_when_not_signed():
    svc = _service(_application())

    result = await svc.handle_sign_callback(
        {
            "status": "EXPIRED",
            "documents": [{"sign_process_id": "pid-1"}, {"sign_process_id": "pid-2"}],
        }
    )

    assert result["documents"] == [
        {"sign_process_id": "pid-1", "status": "ok"},
        {"sign_process_id": "pid-2", "status": "ok"},
    ]
    svc.repository.get_application_by_sign_process_id.assert_not_called()


def test_extract_iin_from_dn_ignores_bin():
    dn_name = "CN=TOO, SERIALNUMBER=BIN123456789012, SERIALNUMBER=IIN040516551071"
    assert FactoringService._extract_iin_from_dn(dn_name) == "040516551071"


def test_parse_sign_batch_response():
    sign_url, documents = parse_sign_batch_response(
        {
            "data": {
                "sign_url": "https://nca.example/sign/batch-1",
                "documents": [
                    {"file_name": "factoring_application.pdf", "sign_process_id": "pid-1", "group_id": "g-1"},
                    {"file_name": "factoring_notification.pdf", "sign_process_id": "pid-2", "group_id": "g-2"},
                ],
            }
        }
    )

    assert sign_url == "https://nca.example/sign/batch-1"
    assert documents[0]["sign_process_id"] == "pid-1"
    assert documents[1]["group_id"] == "g-2"


def test_parse_sign_batch_response_requires_sign_url():
    with pytest.raises(MyncaClientError):
        parse_sign_batch_response({"data": {"documents": [{"sign_process_id": "pid-1"}]}})


@pytest.mark.asyncio
async def test_prepare_print_forms_opens_one_batch():
    mynca = MagicMock()
    mynca.sign_batch = AsyncMock(
        return_value=(
            "https://nca.example/sign/batch-1",
            [
                {"file_name": "factoring_application.pdf", "sign_process_id": "pid-1", "group_id": "g-1"},
                {"file_name": "factoring_notification.pdf", "sign_process_id": "pid-2", "group_id": "g-2"},
            ],
        )
    )
    svc = FactoringService(
        repository=MagicMock(),
        client=MagicMock(),
        app_env="test",
        mynca=mynca,
        public_base_url="https://devintegration.smart-remont.kz",
    )
    svc._render_print_form_pdf = AsyncMock(side_effect=[b"pdf-1", b"pdf-2"])

    forms = await svc._prepare_print_forms(placeholders={}, client_request_id=2910146)

    mynca.sign_batch.assert_awaited_once()
    payload = mynca.sign_batch.await_args.kwargs
    assert payload["atomic"] is True
    assert [item["file_name"] for item in payload["documents"]] == [
        "factoring_application.pdf",
        "factoring_notification.pdf",
    ]
    assert forms[0]["sign_url"] == forms[1]["sign_url"] == "https://nca.example/sign/batch-1"
    assert forms[0]["sign_process_id"] == "pid-1"
    assert forms[1]["sign_process_id"] == "pid-2"
