import io
import re
import zipfile
from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException

from src.features.factoring.ff.cession_placeholders import (
    bare_company_name,
    build_cession_placeholders,
    product_name,
    signer_names,
)
from src.features.factoring.ff.docx_fill import fill_docx_bytes
from src.features.factoring.ff.repo import CessionBatchItem, FactoringProvider
from src.features.factoring.ff.service import FactoringService
from src.features.factoring.schemas import CessionPreviewRequest, SendCessionRequest

NBSP = "\u00a0"
ALMATY = ZoneInfo("Asia/Almaty")


def _docx(body: str) -> bytes:
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", xml)
    return buffer.getvalue()


def _document_xml(docx: bytes) -> str:
    return zipfile.ZipFile(io.BytesIO(docx)).read("word/document.xml").decode()


def _texts(xml: str) -> list[str]:
    return re.findall(r"<w:t[^>]*>([^<]*)</w:t>", xml)


def _build(**overrides):
    params = {
        "contract_number": "ЦЕС-2026-0925-8",
        "issue_date": date(2026, 9, 25),
        "framework_num": "А0-17/703",
        "framework_date": date(2026, 9, 4),
        "signing_date": date(2026, 9, 28),
        "company_name": "ТОО «Smart Remont Azure»",
        "company_iik": "KZ9696503F0015892004",
        "client_signer": "Усембекова Шынар Сериковна",
        "applications": [
            {
                "uuid": "uuid-1",
                "credit_contract": "FCT-1",
                "principal": Decimal("2000000"),
                "period": 12,
                "issued_at": datetime(2026, 9, 25, 11, 42, tzinfo=ALMATY),
            },
            {
                "uuid": "uuid-2",
                "credit_contract": "FCT-2",
                "principal": Decimal("850000"),
                "period": 3,
                "issued_at": datetime(2026, 9, 25, 15, 5, tzinfo=ALMATY),
            },
        ],
    }
    params.update(overrides)
    return build_cession_placeholders(**params)


def test_signer_names_decline_russian_surnames_and_keep_kazakh_forms() -> None:
    assert signer_names("Усембекова Шынар Сериковна") == {
        "short_ru": "Усембекова Ш.С.",
        "short_ru_gen": "Усембековой Ш.С.",
        "short_kz": "Ш.С. Усембекова",
    }
    assert signer_names("Искаков Сырымбет Хасанович")["short_ru_gen"] == "Искакова С.Х."
    assert signer_names("Петрущак Николай Олегович")["short_ru_gen"] == "Петрущака Н.О."
    assert signer_names("Аманкелдіұлы Рустем")["short_ru_gen"] == "Аманкелдіұлы Р."


def test_bare_company_name_strips_legal_form_and_quotes() -> None:
    assert bare_company_name("ТОО «Smart Remont Azure»") == "Smart Remont Azure"
    assert bare_company_name('ТОО "Smart Remont"') == "Smart Remont"


def test_product_name_uses_correct_russian_plural() -> None:
    assert product_name(3) == "Smart Remont факторинг 3 месяца"
    assert product_name(12) == "Smart Remont факторинг 12 месяцев"
    assert product_name(24) == "Smart Remont факторинг 24 месяца"


def test_cession_sums_financing_is_claims_minus_discount() -> None:
    header, rows = _build()

    assert header["claims_sum"] == f"2{NBSP}850{NBSP}000"
    assert header["total_discount_sum"] == f"278{NBSP}250"
    assert header["financing_sum"] == f"2{NBSP}571{NBSP}750"
    assert header["assignment_sum"] == header["claims_sum"]
    assert header["assignment_sum_words_ru"] == "два миллиона восемьсот пятьдесят тысяч"
    assert header["assignment_sum_words_kz"] == "екі миллион сегіз жүз елу мың"
    assert header["financing_sum_words_ru"] == "два миллиона пятьсот семьдесят одна тысяча семьсот пятьдесят"
    assert header["financing_sum_words_kz"] == "екі миллион бес жүз жетпіс бір мың жеті жүз елу"

    first, second = rows
    assert first["tariff"] == "12%"
    assert first["discount_sum"] == f"240{NBSP}000"
    assert first["purchase_sum"] == first["contract_sum"]
    assert first["financing_sum"] == f"1{NBSP}760{NBSP}000"
    assert second["tariff"] == "4,5%"
    assert second["term_kz"] == "3 ай"
    assert first["partner_ru"] == "ТОО «Smart Remont Azure»"
    assert first["partner_kz"] == "«Smart Remont Azure» ЖШС"
    assert first["status_kz"] == "Берілді"


def test_cession_header_dates_and_signer() -> None:
    header, _ = _build()

    assert header["client_name"] == "Smart Remont Azure"
    assert header["application_date_ru"] == "«28» сентября 2026 года"
    assert header["sale_date_ru"] == "«25» сентября 2026 г."
    assert header["appendix_date_kz"] == "2026 жылғы «25» қыркүйектегі"
    assert header["valid_from_date"] == "25.09.2026"
    assert header["client_signer_short_ru_gen"] == "Усембековой Ш.С."
    assert header["client_basis_ru"].endswith("(тест)")
    assert header["client_basis_kz"].endswith("сенімхат (тест)")


def test_cession_framework_contract_of_company() -> None:
    header, _ = _build()

    assert header["framework_num"] == "А0-17/703"
    assert header["framework_date_ru"] == "«04» сентября 2026"
    assert header["framework_date_kz"] == "2026 жылғы «04» қыркүйектегі"


def test_cession_signer_config_replaces_test_power_of_attorney() -> None:
    header, _ = _build(
        signer_config={"poa_num": "15-2026", "poa_date": "2026-01-10", "fio_ru_gen": "Усембековой Шынар Сериковны"}
    )

    assert header["client_basis_ru"] == "Доверенности № 15-2026 от 10 января 2026 г."
    assert header["client_basis_kz"] == "2026 жылғы 10 қаңтардағы № 15-2026 сенімхат"
    assert header["client_signer_short_ru_gen"] == "Усембековой Шынар Сериковны"


def test_fill_keeps_formatting_of_other_runs() -> None:
    body = (
        "<w:p>"
        '<w:r><w:rPr><w:b/></w:rPr><w:t>ТОО «</w:t></w:r>'
        "<w:r><w:t>{{ client_</w:t></w:r><w:r><w:t>name }}</w:t></w:r>"
        '<w:r><w:rPr><w:b/></w:rPr><w:t>»</w:t></w:r>'
        "</w:p>"
    )

    xml = _document_xml(fill_docx_bytes(_docx(body), {"client_name": "Smart Remont"}))

    assert _texts(xml) == ["ТОО «", "Smart Remont", "", "»"]
    assert xml.count("<w:b/>") == 2


def test_fill_repeats_item_rows() -> None:
    body = (
        "<w:tbl>"
        "<w:tr><w:tc><w:p><w:r><w:t>№</w:t></w:r></w:p></w:tc></w:tr>"
        "<w:tr><w:tc><w:p><w:r><w:t>{{ item.num }}</w:t></w:r></w:p></w:tc>"
        "<w:tc><w:p><w:r><w:t>{{ item.contract_num }}</w:t></w:r></w:p></w:tc></w:tr>"
        "<w:tr><w:tc><w:p><w:r><w:t>{{ financing_sum }}</w:t></w:r></w:p></w:tc></w:tr>"
        "</w:tbl>"
    )
    header, rows = _build()

    xml = _document_xml(fill_docx_bytes(_docx(body), header, row_values=rows))

    assert _texts(xml) == ["№", "1", "FCT-1", "2", "FCT-2", header["financing_sum"]]


def _batch_item(item_id: int, company_id: int) -> CessionBatchItem:
    return CessionBatchItem(
        id=item_id,
        uuid=f"uuid-{item_id}",
        credit_contract=f"FCT-{item_id}",
        principal=Decimal("1000000"),
        status="ISSUED",
        company_id=company_id,
        period=12,
        issued_at=datetime(2026, 9, 25, 11, 0, tzinfo=ALMATY),
    )


PROVIDER_CONFIG = {
    "partner_by_company_id": {"8": "FACTORING_AZ", "9": "FACTORING_SO"},
    "framework_contract_by_company_id": {
        "8": {"num": "А0-17/703", "date": "2026-09-04"},
        "9": {"num": "А0-17/704", "date": "2026-09-04"},
    },
}


def _preview_service(
    items: list[CessionBatchItem], config: dict | None = None
) -> FactoringService:
    repository = MagicMock()
    repository.list_cession_batch = AsyncMock(return_value=items)
    repository.get_provider_by_code = AsyncMock(
        return_value=FactoringProvider(
            id=1,
            code="FF_FACTORING",
            base_url="https://bank",
            config=PROVIDER_CONFIG if config is None else config,
        )
    )
    repository.get_cession_company = AsyncMock(
        return_value={"company_name_official": "ТОО «Smart Remont Azure»", "bank_account": "KZ00", "director_fio": "Усембекова Шынар Сериковна"}
    )
    repository.mark_cession_sent = AsyncMock()
    client = MagicMock()
    client.send_cession = AsyncMock()
    service = FactoringService(repository=repository, client=client, app_env="test")
    service._build_cession_document = AsyncMock(return_value=b"%PDF-preview")
    return service


def _yesterday() -> str:
    return (datetime.now(ALMATY).date() - timedelta(days=1)).isoformat()


@pytest.mark.asyncio
async def test_cession_preview_returns_pdf_without_sign_or_bank() -> None:
    service = _preview_service([_batch_item(1, 8)])
    issue_date = _yesterday()

    pdf, filename = await service.preview_cession(CessionPreviewRequest(issue_date=issue_date))

    assert pdf == b"%PDF-preview"
    assert filename == f"cession-{issue_date}-8.pdf"
    kwargs = service._build_cession_document.await_args.kwargs
    assert kwargs["framework_num"] == "А0-17/703"
    assert kwargs["framework_date"] == date(2026, 9, 4)
    service.client.send_cession.assert_not_awaited()
    service.repository.mark_cession_sent.assert_not_awaited()


@pytest.mark.asyncio
async def test_cession_preview_422_without_framework_contract() -> None:
    config = {"partner_by_company_id": {"8": "FACTORING_AZ"}}
    service = _preview_service([_batch_item(1, 8)], config=config)

    with pytest.raises(HTTPException) as exc:
        await service.preview_cession(CessionPreviewRequest(issue_date=_yesterday()))

    assert exc.value.status_code == 422
    service._build_cession_document.assert_not_awaited()


@pytest.mark.asyncio
async def test_cession_send_skips_company_without_framework_contract() -> None:
    config = {
        "partner_by_company_id": {"8": "FACTORING_AZ", "10": "FACTORING_VI"},
        "framework_contract_by_company_id": {"8": {"num": "А0-17/703", "date": "2026-09-04"}},
    }
    service = _preview_service([_batch_item(1, 8), _batch_item(2, 10)], config=config)

    response = await service.send_daily_cession(
        SendCessionRequest(issue_date=_yesterday(), dry_run=True)
    )

    by_company = {batch.company_id: batch for batch in response.batches}
    assert by_company[8].bank_message.startswith("dry_run")
    assert by_company[10].sent is False
    assert "framework_contract_by_company_id" in by_company[10].bank_message
    service.client.send_cession.assert_not_awaited()


@pytest.mark.asyncio
async def test_cession_preview_requires_company_when_several() -> None:
    service = _preview_service([_batch_item(1, 8), _batch_item(2, 9)])

    with pytest.raises(HTTPException) as exc:
        await service.preview_cession(CessionPreviewRequest(issue_date=_yesterday()))

    assert exc.value.status_code == 409
    service._build_cession_document.assert_not_awaited()


@pytest.mark.asyncio
async def test_cession_preview_404_when_nothing_issued() -> None:
    service = _preview_service([])

    with pytest.raises(HTTPException) as exc:
        await service.preview_cession(CessionPreviewRequest(issue_date=_yesterday()))

    assert exc.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("issue_date", [None, "2999-01-01"])
async def test_cession_rejects_today_and_future(issue_date: str | None) -> None:
    service = _preview_service([_batch_item(1, 8)])
    if issue_date is None:
        issue_date = datetime.now(ALMATY).date().isoformat()

    for request in (
        CessionPreviewRequest(issue_date=issue_date),
        SendCessionRequest(issue_date=issue_date),
    ):
        with pytest.raises(HTTPException) as exc:
            if isinstance(request, CessionPreviewRequest):
                await service.preview_cession(request)
            else:
                await service.send_daily_cession(request)
        assert exc.value.status_code == 422

    service.repository.list_cession_batch.assert_not_awaited()
