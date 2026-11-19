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


def _payload(credit_goods=None, **request_fields) -> dict:
    request = CreateFactoringApplicationRequest(
        client_request_id=3163701,
        iin="040516551071",
        mobile_phone="+77057238447",
        principal=Decimal("1000000"),
        period=12,
        created_by=1,
        print_forms=[PrintFormItem(name="application", url="https://example.test/a.pdf")],
        **request_fields,
    )
    return _service()._build_apply_payload(
        provider=MagicMock(),
        iin="040516551071",
        phone="77057238447",
        product_id="FACTORING_SR",
        partner="FACTORING_AZ",
        channel="AVIATA_FAC_WEB",
        credit_contract="FCT26-300000-3163701",
        request=request,
        print_forms=[{"name": "application", "url": "https://example.test/a.pdf"}],
        credit_goods=credit_goods,
        reference_id="7",
        hook_url="https://hook.test/",
        success_url="https://ok.test/",
        failure_url="https://fail.test/",
    )


def test_apply_payload_always_sends_prepayment_and_goods():
    payload = _payload()

    assert payload["credit_params"]["prepayment_amount"] == 0.0
    assert payload["credit_goods"] == [
        {
            "cost": 1000000.0,
            "good_identifier": "3163701",
            "quantity": 1,
            "brand": "Smart Remont",
            "model": "Ремонт квартиры",
            "category": "Ремонт",
        }
    ]


def test_apply_payload_keeps_real_prepayment():
    payload = _payload(prepayment_amount=Decimal("250000"))

    assert payload["credit_params"]["prepayment_amount"] == 250000.0


def test_apply_payload_keeps_goods_stored_on_the_application():
    goods = [{"cost": 500000.0, "good_identifier": "G-1", "quantity": 2}]

    assert _payload(credit_goods=goods)["credit_goods"] == goods
