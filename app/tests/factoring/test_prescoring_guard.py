from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from src.features.factoring.ff.limits import phone_for_prescoring
from src.features.factoring.ff.repo import FactoringProvider
from src.features.factoring.ff.service import FactoringService, _PrescoringOutcome
from src.features.factoring.schemas import FactoringApplicationResponse


def _provider(*, required: bool) -> FactoringProvider:
    return FactoringProvider(
        id=2,
        code="FF_FACTORING",
        base_url="https://example.test/",
        config={"prescoring_required": required},
    )


def _service() -> FactoringService:
    return FactoringService(
        repository=MagicMock(),
        client=MagicMock(),
        app_env="test",
    )


def test_prescoring_required_blocks_skipped_outcome():
    svc = _service()
    outcome = _PrescoringOutcome(None, None, None, None, True, None)

    with pytest.raises(HTTPException) as exc:
        svc._require_prescoring_outcome(_provider(required=True), outcome)

    assert exc.value.status_code == 422


def test_prescoring_required_blocks_non_approved_outcome():
    svc = _service()
    outcome = _PrescoringOutcome(
        "PENDING",
        0.5,
        "На рассмотрении",
        None,
        False,
        datetime.now(UTC),
    )

    with pytest.raises(HTTPException) as exc:
        svc._require_prescoring_outcome(_provider(required=True), outcome)

    assert exc.value.status_code == 422
    assert exc.value.detail == "На рассмотрении"


def test_prescoring_required_allows_approved_outcome():
    svc = _service()
    outcome = _PrescoringOutcome(
        "APPROVED",
        0.9,
        None,
        Decimal("100000"),
        False,
        datetime.now(UTC),
    )

    svc._require_prescoring_outcome(
        _provider(required=True),
        outcome,
        principal=Decimal("50000"),
    )


def test_prescoring_optional_allows_skipped_outcome():
    svc = _service()
    outcome = _PrescoringOutcome(None, None, None, None, True, None)

    svc._require_prescoring_outcome(_provider(required=False), outcome)


def test_prescoring_optional_blocks_rejected_outcome():
    svc = _service()
    outcome = _PrescoringOutcome(
        "REJECTED",
        0.1,
        "Отказ",
        None,
        False,
        datetime.now(UTC),
    )

    with pytest.raises(HTTPException) as exc:
        svc._require_prescoring_outcome(_provider(required=False), outcome)

    assert exc.value.status_code == 422


def test_stored_prescoring_required_blocks_missing_status():
    svc = _service()
    application = FactoringApplicationResponse(
        id=1,
        client_request_id=100,
        provider_code="FF_FACTORING",
        uuid="u",
        reference_id="r",
        credit_contract="c",
        status="WAITING_SIGN",
        prescoring_status=None,
        prescoring_checked_at=None,
    )

    with pytest.raises(HTTPException) as exc:
        svc._require_stored_prescoring(_provider(required=True), application)

    assert exc.value.status_code == 422


def test_stored_prescoring_required_blocks_stale_check():
    svc = _service()
    application = FactoringApplicationResponse(
        id=1,
        client_request_id=100,
        provider_code="FF_FACTORING",
        uuid="u",
        reference_id="r",
        credit_contract="c",
        status="WAITING_SIGN",
        prescoring_status="APPROVED",
        prescoring_checked_at=datetime.now(UTC) - timedelta(hours=2),
    )

    with pytest.raises(HTTPException) as exc:
        svc._require_stored_prescoring(_provider(required=True), application)

    assert exc.value.status_code == 422
    assert "устарел" in exc.value.detail.lower()


def test_prescoring_auth_uses_partner_credentials_from_config():
    provider = FactoringProvider(
        id=2,
        code="FF_FACTORING",
        base_url="https://example.test/",
        config={
            "prescoring_base_url": "http://10.48.66.1:8081",
            "prescoring_credentials": {
                "FACTORING_AZ": {"user": "smart_remont_azure", "password": "az-secret"},
                "FACTORING_NO": {"user": "smart_remont_north", "password": "no-secret"},
            },
        },
    )
    svc = FactoringService(
        repository=MagicMock(),
        client=MagicMock(),
        app_env="test",
        prescoring_username="env-user",
        prescoring_password="env-secret",
    )

    assert svc._prescoring_auth(provider, "FACTORING_AZ") == (
        "smart_remont_azure",
        "az-secret",
    )
    assert svc._is_prescoring_configured(provider, "FACTORING_NO") is True


def test_prescoring_auth_falls_back_to_env_when_partner_missing():
    provider = FactoringProvider(
        id=2,
        code="FF_FACTORING",
        base_url="https://example.test/",
        config={
            "prescoring_base_url": "http://10.48.66.1:8081",
            "prescoring_credentials": {
                "FACTORING_AZ": {"user": "smart_remont_azure", "password": "az-secret"},
            },
        },
    )
    svc = FactoringService(
        repository=MagicMock(),
        client=MagicMock(),
        app_env="test",
        prescoring_username="env-user",
        prescoring_password="env-secret",
    )

    assert svc._prescoring_auth(provider, "FACTORING_SO") == ("env-user", "env-secret")


def test_prescoring_enabled_when_only_config_credentials_exist():
    provider = FactoringProvider(
        id=2,
        code="FF_FACTORING",
        base_url="https://example.test/",
        config={
            "prescoring_base_url": "http://10.48.66.1:8081",
            "prescoring_credentials": {
                "FACTORING_VI": {"user": "smart_remont_viva", "password": "vi-secret"},
            },
        },
    )
    svc = _service()

    assert svc._is_prescoring_configured(provider) is True
    assert svc._prescoring_auth(provider, "FACTORING_AZ") is None


def test_stored_prescoring_required_allows_fresh_approved():
    svc = _service()
    application = FactoringApplicationResponse(
        id=1,
        client_request_id=100,
        provider_code="FF_FACTORING",
        uuid="u",
        reference_id="r",
        credit_contract="c",
        status="WAITING_SIGN",
        prescoring_status="APPROVED",
        prescoring_checked_at=datetime.now(UTC) - timedelta(minutes=5),
    )

    svc._require_stored_prescoring(_provider(required=True), application)


def test_phone_for_prescoring_keeps_leading_seven():
    assert phone_for_prescoring("+77057238447") == "77057238447"
    assert phone_for_prescoring("7057238447") == "77057238447"
    assert phone_for_prescoring("87057238447") == "77057238447"
