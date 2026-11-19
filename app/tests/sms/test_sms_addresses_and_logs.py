"""SMS crons: Zend address forms, and what must not reach the logs."""

import httpx
import pytest
from asyncpg.exceptions import RaiseError
from httpx import AsyncClient

from tests.sms.fakes import (
    NOTIFY_ROUTE,
    PHONE,
    READ_BATCHES,
    READ_QUEUE,
    SET_RESULT,
    SET_STATUS,
    STATUS_ROUTE,
    TEXT,
    Database,
    Kcell,
    assert_text,
    queue_row,
)


@pytest.mark.parametrize("path", [NOTIFY_ROUTE, f"{NOTIFY_ROUTE}/", f"{NOTIFY_ROUTE}/key/value"])
async def test_zend_address_forms_and_any_method_work_as_in_php(
    client: AsyncClient,
    path: str,
) -> None:
    assert_text(await client.get(path), b"0")
    assert_text(await client.post(path), b"0")


@pytest.mark.parametrize("path", [NOTIFY_ROUTE, f"{NOTIFY_ROUTE}/key/value"])
async def test_every_address_form_checks_the_caller(
    client: AsyncClient,
    kcell: Kcell,
    path: str,
) -> None:
    client.headers.pop("Authorization")

    assert (await client.post(path)).status_code == 401
    assert kcell.requests == []


async def test_logs_have_no_phone_and_no_text(
    client: AsyncClient,
    db: Database,
    kcell: Kcell,
    log_lines: list[str],
) -> None:
    failing = f"invalid input: {PHONE} {TEXT}"
    db.responses[READ_QUEUE] = [queue_row(number) for number in range(1, 402)]
    db.responses[SET_RESULT] = RaiseError(failing)
    db.responses[SET_STATUS] = RaiseError(failing)
    answers = iter(
        [
            httpx.ReadTimeout(failing),
            httpx.Response(
                500, text=f'{{"error": "{PHONE} {TEXT}", "results": [{{"to": "{PHONE}"}}]}}'
            ),
            httpx.Response(200, json={"messages": [{"to": PHONE}]}),
        ],
    )

    def answer(_: httpx.Request) -> httpx.Response:
        result = next(answers)
        if isinstance(result, Exception):
            raise result
        return result

    kcell.answer = answer

    await client.get(NOTIFY_ROUTE)
    await client.get(STATUS_ROUTE)

    logged = "".join(log_lines)
    assert "messages stay queued" in logged
    assert PHONE not in logged
    assert TEXT not in logged


async def test_read_failure_log_has_no_database_text(
    client: AsyncClient,
    db: Database,
    log_lines: list[str],
) -> None:
    db.responses[READ_QUEUE] = RaiseError(f"{{bad {PHONE}}}")
    db.responses[READ_BATCHES] = RaiseError(f"{{bad {PHONE}}}")

    await client.get(NOTIFY_ROUTE)
    await client.get(STATUS_ROUTE)

    assert "sms_notify__read" in "".join(log_lines)
    assert PHONE not in "".join(log_lines)


async def test_failed_status_on_a_recorded_batch_is_logged_with_the_batch_only(
    client: AsyncClient,
    kcell: Kcell,
    log_lines: list[str],
) -> None:
    def answer(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout(PHONE)

    kcell.answer = answer

    await client.get(STATUS_ROUTE)

    logged = "".join(log_lines)
    assert "B1" in logged
    assert PHONE not in logged
