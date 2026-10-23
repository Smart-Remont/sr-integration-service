from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from src.features.factoring.ff.service import FactoringService
from src.features.factoring.schemas import FactoringApplicationResponse


def _service() -> FactoringService:
    return FactoringService(
        repository=MagicMock(),
        client=MagicMock(),
        app_env="test",
    )


def test_sanitize_print_form_strips_secrets():
    svc = _service()
    cleaned = svc._sanitize_print_form_for_client(
        {
            "name": "application",
            "file_token": "secret-token",
            "url_expires_at": "2099-01-01T00:00:00+00:00",
            "url": "https://example.test/leak",
            "signed": True,
        }
    )
    assert "file_token" not in cleaned
    assert "url_expires_at" not in cleaned
    assert cleaned["url"] == ""


def test_print_form_link_expired():
    svc = _service()
    with pytest.raises(HTTPException) as exc:
        svc._require_print_form_link_active(
            {"url_expires_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat()}
        )
    assert exc.value.status_code == 410


def test_sanitize_application_for_client():
    svc = _service()
    application = FactoringApplicationResponse(
        id=1,
        client_request_id=100,
        provider_code="FF_FACTORING",
        uuid="u",
        reference_id="r",
        credit_contract="c",
        status="WAITING_SIGN",
        print_forms=[
            {
                "name": "application",
                "file_token": "secret",
                "url": "https://example.test/leak",
            }
        ],
    )
    sanitized = svc._sanitize_application_for_client(application)
    assert "file_token" not in sanitized.print_forms[0]
    assert sanitized.print_forms[0]["url"] == ""
