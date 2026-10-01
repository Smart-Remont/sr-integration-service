from decimal import Decimal
from unittest.mock import MagicMock

from src.features.factoring.ff.service import FactoringService
from src.features.factoring.schemas import CreateFactoringApplicationRequest, PrintFormItem


def _service() -> FactoringService:
    return FactoringService(repository=MagicMock(), client=MagicMock(), app_env="test")


def test_apply_payload_includes_prescoring_in_credit_configs():
    request = CreateFactoringApplicationRequest(
        client_request_id=3203456,
        iin="040516551071",
        mobile_phone="+77057238447",
        principal=Decimal("1000000"),
        period=12,
        created_by=1,
        print_forms=[PrintFormItem(name="application", url="https://example.test/a.pdf")],
    )
    payload = _service()._build_apply_payload(
        provider=MagicMock(),
        iin="040516551071",
        phone="77057238447",
        product_id="FACTORING_SR",
        partner="FACTORING_AZ",
        channel="AVIATA_FAC_WEB",
        credit_contract="FCT26-300000-3203456",
        request=request,
        print_forms=[{"name": "application", "url": "https://example.test/a.pdf"}],
        credit_goods=None,
        reference_id="7",
        hook_url="https://hook.test/",
        success_url="https://ok.test/",
        failure_url="https://fail.test/",
        prescoring_score=Decimal("0.003028680904588188"),
        prescoring_max_limit=Decimal("3000000"),
    )
    assert payload["credit_configs"] == {
        "is_knox": False,
        "score": 0.003028680904588188,
        "max_limit": 3000000.0,
    }
