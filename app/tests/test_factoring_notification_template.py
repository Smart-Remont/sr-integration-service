from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from src.features.factoring.ff.repo import FactoringProvider
from src.features.factoring.ff.service import FactoringService, _NotificationContext

CONFIG = {
    "notification_template_by_company_id": {"8": "FF_FACTORING_NOTIFICATION_AZURE"},
    "framework_contract_by_company_id": {"8": {"num": "А0-17/703", "date": "2026-09-04"}},
}


def _provider(config: dict | None = None) -> FactoringProvider:
    return FactoringProvider(id=1, code="FF_FACTORING", base_url="https://example.test/", config=config or CONFIG)


def _service(
    *,
    company_id: int | None = 8,
    company: dict | None = None,
    template: dict | None = None,
) -> FactoringService:
    repository = MagicMock()
    repository.get_client_request_company_id = AsyncMock(return_value=company_id)
    repository.get_cession_company = AsyncMock(
        return_value=company
        if company is not None
        else {"company_name_official": "ТОО «Smart Remont Azure»", "director_fio": "Усембекова Шынар Сериковна"}
    )
    repository.get_template_by_code = AsyncMock(
        return_value=template if template is not None else {"template_path": "/documents/x.docx"}
    )
    return FactoringService(repository=repository, client=MagicMock(), app_env="test")


@pytest.mark.asyncio
async def test_notification_context_uses_company_template_and_requisites():
    svc = _service()

    ctx = await svc._require_notification_context(_provider(), 3163701)

    assert ctx == _NotificationContext(
        template_code="FF_FACTORING_NOTIFICATION_AZURE",
        partner_name="Smart Remont Azure",
        partner_signer="Усембекова Шынар Сериковна",
        framework_num="А0-17/703",
        framework_date=date(2026, 9, 4),
    )
    svc.repository.get_template_by_code.assert_awaited_once_with("FF_FACTORING_NOTIFICATION_AZURE")


@pytest.mark.asyncio
async def test_notification_context_requires_template_mapping_for_company():
    svc = _service(company_id=9)

    with pytest.raises(HTTPException) as exc:
        await svc._require_notification_context(_provider(), 1)

    assert exc.value.status_code == 422
    assert "шаблон уведомления" in exc.value.detail


@pytest.mark.asyncio
async def test_notification_context_requires_framework_contract():
    config = {"notification_template_by_company_id": {"8": "FF_FACTORING_NOTIFICATION_AZURE"}}
    svc = _service()

    with pytest.raises(HTTPException) as exc:
        await svc._require_notification_context(_provider(config), 1)

    assert exc.value.status_code == 422
    assert "рамочный договор" in exc.value.detail


@pytest.mark.asyncio
async def test_notification_context_requires_director():
    svc = _service(company={"company_name_official": "ТОО «Smart Remont Azure»", "director_fio": ""})

    with pytest.raises(HTTPException) as exc:
        await svc._require_notification_context(_provider(), 1)

    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_notification_context_requires_uploaded_template():
    svc = _service(template={"template_path": ""})

    with pytest.raises(HTTPException) as exc:
        await svc._require_notification_context(_provider(), 1)

    assert exc.value.status_code == 422
    assert "не загружен" in exc.value.detail


def test_template_values_fill_partner_framework_and_amount_words():
    ctx = _NotificationContext(
        template_code="FF_FACTORING_NOTIFICATION_AZURE",
        partner_name="Smart Remont Azure",
        partner_signer="Усембекова Шынар Сериковна",
        framework_num="А0-17/703",
        framework_date=date(2026, 9, 4),
    )

    values = FactoringService._template_values(
        client_fio="Кимадиев Бекжан",
        iin="040516551071",
        phone="+77057238447",
        principal=Decimal("1200000"),
        period=12,
        credit_contract="FCT26-200000-3204488",
        client_request_id=3204488,
        notification=ctx,
    )

    assert values["principal"] == "1 200 000"
    assert values["principal_ru"] == "один миллион двести тысяч"
    assert values["principal_kz"] == "бір миллион екі жүз мың"
    assert values["partner_name"] == "Smart Remont Azure"
    assert values["partner_signer"] == "Усембекова Шынар Сериковна"
    assert values["framework_num"] == "А0-17/703"
    assert values["framework_date"] == "04.09.2026"
