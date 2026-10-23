from decimal import Decimal
from unittest.mock import MagicMock

from src.features.factoring.ff.service import FactoringService, _PrescoringOutcome


def _service() -> FactoringService:
    return FactoringService(repository=MagicMock(), client=MagicMock(), app_env="test")


def test_approved_over_limit_explains_reason():
    outcome = _PrescoringOutcome(
        "APPROVED", 0.005, "Заявка обработана.", Decimal("2000000"), False, None
    )

    response = _service()._prescoring_response(outcome, Decimal("2100000"))

    assert response.allowed is False
    assert response.message == (
        "Сумма 2 100 000 ₸ превышает лимит прескоринга 2 000 000 ₸. Уменьшите сумму займа."
    )


def test_approved_within_limit_keeps_bank_message():
    outcome = _PrescoringOutcome(
        "APPROVED", 0.005, "Заявка обработана.", Decimal("2000000"), False, None
    )

    response = _service()._prescoring_response(outcome, Decimal("1500000"))

    assert response.allowed is True
    assert response.message == "Заявка обработана."
