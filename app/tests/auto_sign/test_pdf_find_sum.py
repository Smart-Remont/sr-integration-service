"""``pdf-find-sum``: the sum of a contractor agreement against the text of its signed PDF."""

import httpx
import pymupdf
import pytest
from asyncpg.exceptions import RaiseError
from httpx import AsyncClient

from src.features.auto_sign import agreement_sum
from src.features.auto_sign.agreement_sum import sum_is_in_text
from tests.auto_sign.conftest import Partner
from tests.auto_sign.fakes import (
    LOCK,
    PARTNER_URL,
    PDF_ROUTE,
    PROCESS_AGREEMENT,
    READ_AGREEMENTS,
    Database,
    agreement_row,
    assert_empty,
)


def pdf_with(text: str) -> bytes:
    document = pymupdf.open()
    document.new_page().insert_text((50, 50), text)
    content = document.tobytes()
    document.close()
    return content


def pdf_answer(text: str) -> httpx.Response:
    return httpx.Response(200, content=pdf_with(text))


@pytest.mark.parametrize(
    ("text", "is_ok"),
    [
        ("Summa dogovora 1 500 000 tenge", "true"),
        ("Summa dogovora 1500000.00 tenge", "true"),
        ("Summa dogovora 1500001 tenge", "true"),
        ("Summa dogovora 1499999 tenge", "true"),
        ("Summa dogovora 1500002 tenge", "false"),
        ("Summa dogovora 91500000 tenge", "false"),
        ("No sum here", "false"),
    ],
)
async def test_pdf_answer_sets_is_ok(
    client: AsyncClient,
    db: Database,
    partner: Partner,
    text: str,
    is_ok: str,
) -> None:
    partner.answer = pdf_answer(text)

    assert_empty(await client.get(PDF_ROUTE))

    assert db.calls_of(PROCESS_AGREEMENT) == [("41", is_ok)]
    assert db.committed_functions() == [PROCESS_AGREEMENT]


async def test_address_and_call_order(client: AsyncClient, db: Database, partner: Partner) -> None:
    assert_empty(await client.get(PDF_ROUTE))

    assert partner.urls == [f"{PARTNER_URL}/contractor_agreement_list/signed/41/"]
    assert db.events == [
        LOCK,
        "begin",
        READ_AGREEMENTS,
        "commit",
        "begin",
        PROCESS_AGREEMENT,
        "commit",
    ]


@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(200, content=b"<html>not a pdf</html>"),
        httpx.Response(200, content=b""),
        httpx.Response(404, content=b"%PDF-1.7"),
        httpx.ConnectError("no route"),
        httpx.Response(200, content=b"%PDF-1.7 broken"),
    ],
)
async def test_anything_but_a_readable_pdf_sets_null(
    client: AsyncClient,
    db: Database,
    partner: Partner,
    answer: object,
) -> None:
    partner.answer = answer

    assert_empty(await client.get(PDF_ROUTE))

    assert db.calls_of(PROCESS_AGREEMENT) == [("41", None)]


async def test_file_over_the_size_cap_sets_null_without_parsing(
    client: AsyncClient,
    db: Database,
    partner: Partner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    partner.answer = pdf_answer("Summa dogovora 1500000 tenge")
    monkeypatch.setattr(agreement_sum, "MAX_PDF_BYTES", 10)

    assert_empty(await client.get(PDF_ROUTE))

    assert db.calls_of(PROCESS_AGREEMENT) == [("41", None)]


def test_size_cap_is_fifty_megabytes() -> None:
    assert agreement_sum.MAX_PDF_BYTES == 50 * 1024 * 1024


async def test_failed_record_is_logged_and_the_loop_goes_on(
    client: AsyncClient,
    db: Database,
    partner: Partner,
    log_lines: list[str],
) -> None:
    db.responses[READ_AGREEMENTS] = [agreement_row(1), agreement_row(2)]
    db.responses[PROCESS_AGREEMENT] = RaiseError("{broken}")

    assert_empty(await client.get(PDF_ROUTE))

    assert len(partner.urls) == 2
    assert len(db.calls_of(PROCESS_AGREEMENT)) == 2
    assert sum(line.startswith("ERROR") for line in log_lines) == 2


async def test_read_failure_ends_quietly(
    client: AsyncClient, db: Database, partner: Partner
) -> None:
    db.responses[READ_AGREEMENTS] = RaiseError("{broken}")

    assert_empty(await client.get(PDF_ROUTE))

    assert partner.urls == []


async def test_nothing_to_check(client: AsyncClient, db: Database, partner: Partner) -> None:
    db.responses[READ_AGREEMENTS] = []

    assert_empty(await client.get(PDF_ROUTE))

    assert partner.urls == []


async def test_overlapping_run_touches_nothing(
    client: AsyncClient,
    db: Database,
    partner: Partner,
) -> None:
    db.lock_is_held = True

    assert_empty(await client.get(PDF_ROUTE))

    assert partner.urls == []


@pytest.mark.usefixtures("short_limits")
@pytest.mark.parametrize("function", [READ_AGREEMENTS, PROCESS_AGREEMENT])
async def test_hung_step_does_not_stop_the_cron(
    client: AsyncClient,
    db: Database,
    function: str,
) -> None:
    db.hung_functions = {function: 1}

    assert_empty(await client.get(PDF_ROUTE))

    assert (db.released, db.lock_is_held) == (1, False)


@pytest.mark.parametrize(
    ("agreement_sum", "text", "found"),
    [
        ("1500000", "сумма 1500000 тенге", True),
        ("1500000", "сумма 1 500 000 тенге", True),
        ("1500000", "сумма 1500000,00 тенге", True),
        ("1500000", "сумма 1500000.50 тенге", True),
        ("1500000", "сумма 1499999 тенге", True),
        ("1500000", "сумма 1500001 тенге", True),
        ("1500000", "сумма 1500002 тенге", False),
        # PCRE without /u reads bytes: a Cyrillic letter next to the digits is a word boundary.
        ("1500000", "тенге1500000тенге", True),
        # A Latin letter or a digit is not.
        ("1500000", "a1500000", False),
        ("1500000", "1500000a", False),
        ("1500000", "21500000", False),
        # Only spaces go before the comparison: the number reads as its first part, out of range.
        ("1500000", "сумма 1\n500\n000 тенге", False),
        ("1500000", "сумма 1\t500\t000 тенге", False),
        # The non-breaking space of PDF text is not \s for PCRE without /u.
        ("1500000", "сумма 1 500 000 тенге", False),
        ("0", "сумма 1 тенге", True),
        ("0", "сумма 2 тенге", False),
        # PHP null - 1 is -1 and an empty alternative matches any word boundary, as (float)"" is 0.
        (None, "any text", True),
        # Without a Latin letter or a digit there is no word boundary for PCRE on bytes either.
        (None, "любой текст", False),
        (None, "", False),
    ],
)
def test_sum_matching_follows_the_php_expression(
    agreement_sum: str | None,
    text: str,
    found: bool,
) -> None:
    assert sum_is_in_text(agreement_sum, text) is found
