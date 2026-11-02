"""``provider-send-email-requests``. wkhtmltopdf is replaced: the program is not on a dev machine."""

import re
from pathlib import Path

import pytest
from asyncpg.exceptions import RaiseError
from httpx import AsyncClient

from src.features.mailing import provider_files
from src.features.mailing.pdf import PdfError
from src.features.mailing.views import tenge_text
from tests.mailing.fakes import (
    PROVIDER_TABLE,
    PROVIDERS,
    QUEUE_FILE,
    QUEUE_PROVIDER,
    Database,
)

ROUTE = "/api/mailing/provider-send-email-requests"
PROVIDER = {"provider_id": 4, "provider_name": "Поставщик", "provider_email": " A@test.invalid "}
REQUEST_ROW = {
    "provider_request_id": 77,
    "remont_id": 5,
    "amount": "1234567.5",
    "resident_name": "ЖК",
    "flat_num": "12",
    "city_name": "Город",
    "date_shipping_plan": "01.11.2026",
    "statuses": "<span>Новая</span>",
}


@pytest.fixture
def pages(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []

    async def fake_pdf(html: str) -> bytes:
        seen.append(html)
        return b"%PDF contour"

    monkeypatch.setattr(provider_files, "html_to_pdf", fake_pdf)
    return seen


def _tables(not_shipped: list[dict[str, object]], not_processed: list[dict[str, object]]) -> object:
    return lambda args: not_shipped if args[0] == "not_shipped" else not_processed


async def test_both_tables_become_two_files_and_one_queue_row(
    client: AsyncClient,
    db: Database,
    pages: list[str],
    tmp_path: Path,
) -> None:
    db.responses[PROVIDERS] = [PROVIDER]
    db.responses[PROVIDER_TABLE] = _tables([REQUEST_ROW], [REQUEST_ROW])

    response = await client.get(ROUTE)

    assert response.status_code == 200
    assert response.content == b""
    assert response.headers["content-type"] == "application/json; text/html; charset=utf-8"
    assert db.calls_of(PROVIDER_TABLE) == [("not_shipped", 4), ("not_processed", 4)]
    assert db.calls_of(QUEUE_PROVIDER) == [(4, " A@test.invalid ")]
    calls = db.calls_of(QUEUE_FILE)
    assert [(item[0], item[1]) for item in calls] == [
        (555, "Неотгруженные заявки.pdf"),
        (555, "Необработанные заявки.pdf"),
    ]
    for _, _, url in calls:
        assert re.fullmatch(
            r"/documents/provider_email_files/email_file_4[0-9a-f]{13}\d\.\d{8}\.pdf", url
        )
        assert (tmp_path / url.lstrip("/")).read_bytes() == b"%PDF contour"
    assert "<h4>Поставщик</h4>" in pages[0]
    assert "<p>Неотгруженные заявки</p>" in pages[0]
    assert "<p>Необработанные заявки</p>" in pages[1]
    assert '<td class="text-right">1 234 567.50</td>' in pages[0]
    assert "<span>Новая</span><br>" in pages[0]


async def test_only_one_table(client: AsyncClient, db: Database, pages: list[str]) -> None:
    db.responses[PROVIDERS] = [PROVIDER]
    db.responses[PROVIDER_TABLE] = _tables([], [REQUEST_ROW])

    await client.get(ROUTE)

    assert [item[1] for item in db.calls_of(QUEUE_FILE)] == ["Необработанные заявки.pdf"]
    assert len(pages) == 1


async def test_no_requests_no_file_no_queue_row(
    client: AsyncClient, db: Database, pages: list[str]
) -> None:
    db.responses[PROVIDERS] = [PROVIDER]

    await client.get(ROUTE)

    assert pages == []
    assert db.calls_of(QUEUE_PROVIDER) == []


async def test_failed_pdf_keeps_the_queue_row_as_php(
    client: AsyncClient,
    db: Database,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    log_lines: list[str],
) -> None:
    async def broken_pdf(_: str) -> bytes:
        raise PdfError("wkhtmltopdf exited with 1")

    monkeypatch.setattr(provider_files, "html_to_pdf", broken_pdf)
    db.responses[PROVIDERS] = [PROVIDER]
    db.responses[PROVIDER_TABLE] = _tables([REQUEST_ROW], [])

    response = await client.get(ROUTE)

    assert response.status_code == 200
    assert len(db.calls_of(QUEUE_FILE)) == 1
    assert not (tmp_path / "documents").exists()
    assert "PDF not written" in "".join(log_lines)


async def test_failed_table_read_counts_as_no_requests(
    client: AsyncClient, db: Database, pages: list[str]
) -> None:
    db.responses[PROVIDERS] = [PROVIDER]
    db.responses[PROVIDER_TABLE] = RaiseError("Отказ")

    response = await client.get(ROUTE)

    assert response.status_code == 200
    assert pages == []


async def test_failed_queue_row_means_no_attachment_rows_and_the_run_goes_on(
    client: AsyncClient, db: Database, pages: list[str]
) -> None:
    db.responses[PROVIDERS] = [PROVIDER, {**PROVIDER, "provider_id": 5}]
    db.responses[PROVIDER_TABLE] = _tables([REQUEST_ROW], [])
    db.responses[QUEUE_PROVIDER] = RaiseError("Отказ")

    await client.get(ROUTE)

    assert len(db.calls_of(QUEUE_PROVIDER)) == 2
    assert db.calls_of(QUEUE_FILE) == []


async def test_overlapping_run_does_nothing(
    client: AsyncClient, db: Database, pages: list[str]
) -> None:
    db.lock_is_held = True
    db.responses[PROVIDERS] = [PROVIDER]

    await client.get(ROUTE)

    assert db.calls_of(PROVIDERS) == []


@pytest.mark.parametrize(
    ("number", "expected"),
    [
        (None, ""),
        ("", ""),
        ("0", "0"),
        ("1000", "1 000"),
        ("1000.0", "1 000"),
        ("1234567.5", "1 234 567.50"),
        ("0.005", "0.01"),
        ("-1500.25", "-1 500.25"),
        # Taken from number_format of PHP 7.4 in the contour: it rounds to 15 digits first.
        ("1.005", "1.01"),
        ("2.675", "2.68"),
        ("1.015", "1.02"),
        ("1234567.895", "1 234 567.90"),
        ("-0.001", "0.00"),
    ],
)
def test_tenge_text(number: str | None, expected: str) -> None:
    assert tenge_text(number) == expected
