"""``send-email-error-db``."""

import email
import email.policy

from asyncpg.exceptions import RaiseError
from httpx import AsyncClient

from src.features.mailing.smtp import SmtpError, SmtpTimeoutError
from tests.mailing.fakes import (
    ERROR_FROM_ADDRESS,
    FAILED_ERROR,
    FROM_ADDRESS,
    MARK_ERROR,
    READ_ERRORS,
    Database,
    MailServer,
)

ROUTE = "/api/mailing/send-email-error-db"


def _row(send_error_id: int, addresses: str = "dev@test.invalid") -> dict[str, object]:
    return {
        "send_error_id": send_error_id,
        "email": addresses,
        "email_content": "Текст ошибки",
        "email_title": "error_db",
    }


async def test_empty_queue(client: AsyncClient, mail: MailServer) -> None:
    response = await client.get(ROUTE)

    assert response.status_code == 200
    assert response.content == b""
    assert mail.letters == []


async def test_one_letter_for_all_addresses_from_the_other_mailbox_without_list_headers(
    client: AsyncClient, db: Database, mail: MailServer
) -> None:
    db.responses[READ_ERRORS] = [_row(5, "a@test.invalid; b@test.invalid ;")]

    await client.get(ROUTE)

    letter = mail.letters[0]
    assert letter.return_path == ERROR_FROM_ADDRESS
    assert letter.recipients == ("a@test.invalid", "b@test.invalid")
    message = email.message_from_bytes(letter.content, policy=email.policy.compat32)
    assert message["From"] == f"SR <{ERROR_FROM_ADDRESS}>"
    assert message["To"] == "a@test.invalid,\n b@test.invalid"
    assert message["List-Unsubscribe"] is None
    # The session itself still logs in to the notify mailbox.
    assert mail.settings[0].username == FROM_ADDRESS
    assert db.calls_of(MARK_ERROR) == [(5, 1)]
    assert db.events.index(MARK_ERROR) < db.events.index("smtp")


async def test_failure_records_prints_and_switches_the_mailbox_as_php(
    client: AsyncClient, db: Database, mail: MailServer
) -> None:
    db.responses[READ_ERRORS] = [_row(5), _row(6), _row(7)]
    mail.refuse[2] = SmtpError("no\r\n", 550)

    response = await client.get(ROUTE)

    assert response.content == b"no\r\n"
    assert db.calls_of(FAILED_ERROR) == [(6, 2, "no\r\n")]
    assert [item.username for item in mail.settings] == [FROM_ADDRESS, ERROR_FROM_ADDRESS]
    assert mail.settings[1].password == "error-pass"
    assert mail.closed == 2
    assert len(mail.letters) == 2


async def test_no_reply_in_time_ends_the_run(
    client: AsyncClient, db: Database, mail: MailServer
) -> None:
    db.responses[READ_ERRORS] = [_row(5), _row(6)]
    mail.refuse[1] = SmtpTimeoutError("smtp.test.invalid has timed out")

    response = await client.get(ROUTE)

    assert response.content == b"smtp.test.invalid has timed out"
    assert mail.attempts == 1
    assert db.calls_of(MARK_ERROR) == [(5, 1)]
    assert len(mail.settings) == 1


async def test_failed_read_prints_the_error_and_ends(
    client: AsyncClient, db: Database, mail: MailServer
) -> None:
    db.responses[READ_ERRORS] = RaiseError("Отказ")

    response = await client.get(ROUTE)

    assert response.status_code == 200
    assert "Отказ".encode() in response.content
    assert mail.settings == []


async def test_failed_mark_stops_the_run_and_nothing_goes(
    client: AsyncClient, db: Database, mail: MailServer
) -> None:
    db.responses[READ_ERRORS] = [_row(5), _row(6)]
    db.responses[MARK_ERROR] = RaiseError("Отказ")

    await client.get(ROUTE)

    assert mail.attempts == 0
    assert db.calls_of(MARK_ERROR) == [(5, 1)]


async def test_overlapping_run_sends_nothing(
    client: AsyncClient, db: Database, mail: MailServer
) -> None:
    db.lock_is_held = True
    db.responses[READ_ERRORS] = [_row(5)]

    await client.get(ROUTE)

    assert db.calls_of(READ_ERRORS) == []
    assert mail.settings == []
