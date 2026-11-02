"""``send-email-new``. Expected values come from reading PHP, the comparison is in the contour."""

import email
import email.policy
from email.message import Message
from pathlib import Path

import pytest
from asyncpg.exceptions import InterfaceError, RaiseError
from httpx import AsyncClient

from src.features.mailing.smtp import SmtpError, SmtpTimeoutError
from src.features.mailing.steps import DatabaseGoneError
from tests.mailing.fakes import (
    CC_ADDRESS,
    CHECK,
    FAILED,
    FROM_ADDRESS,
    HARD,
    INFO,
    LOCK,
    MARK,
    READ_ATTACHMENTS,
    READ_QUEUE,
    TOKEN,
    Database,
    MailServer,
    queue_row,
)

ROUTE = "/api/mailing/send-email-new"


def _parse(raw: bytes) -> Message:
    return email.message_from_bytes(raw, policy=email.policy.compat32)


def _html(raw: bytes) -> str:
    message = _parse(raw)
    for part in message.walk():
        if part.get_content_type() == "text/html":
            return part.get_payload(decode=True).decode()
    raise AssertionError("no html part")


async def test_empty_queue_answers_as_php_and_sends_nothing(
    client: AsyncClient, mail: MailServer
) -> None:
    response = await client.get(ROUTE)

    assert response.status_code == 200
    assert response.content == b""
    assert response.headers["content-type"] == "application/json; text/html; charset=utf-8"
    assert response.headers["cache-control"] == "no-store, no-cache, must-revalidate"
    assert mail.letters == []
    assert mail.closed == 1


async def test_letter_has_the_links_and_the_headers_of_php(
    client: AsyncClient, db: Database, mail: MailServer
) -> None:
    db.responses[READ_QUEUE] = [queue_row(11)]

    response = await client.get(ROUTE)

    assert response.content == b""
    letter = mail.letters[0]
    assert letter.return_path == FROM_ADDRESS
    assert letter.recipients == ("one@test.invalid",)
    message = _parse(letter.content)
    assert message["From"] == f"SmartRemont <{FROM_ADDRESS}>"
    assert message["To"] == "one@test.invalid"
    assert message["Precedence"] == "list"
    assert message["List-Unsubscribe"] == "https://office.test/index/unsubscribe?token=TOK"
    html = _html(letter.content)
    assert "<p>Текст</p>" in html
    assert 'href="https://office.test/index/unsubscribe?token=TOK"' in html
    assert "Фио" in html
    # send_email_tab has no remont_id: the part of the address stays empty, as in PHP.
    assert (
        'href="https://office.test/system/redirect-email/mode/remont/remont_id//email/11/token/REMONT-TOK"'
        in html
    )
    assert "Перейти в ремонт" in html
    assert db.calls_of(TOKEN) == [(7, 0, 11)]
    assert db.calls_of(MARK) == [(11, 1)]


async def test_reset_letter_gets_its_own_link(
    client: AsyncClient, db: Database, mail: MailServer
) -> None:
    db.responses[READ_QUEUE] = [queue_row(11, what="RESET_PASSWORD")]

    await client.get(ROUTE)

    html = _html(mail.letters[0].content)
    assert (
        'href="https://office.test/system/redirect-email/mode/reset_password/email/11/token/REMONT-TOK"'
        in html
    )
    assert "Сбросить" in html


async def test_client_token_is_asked_with_the_client_id(
    client: AsyncClient, db: Database, mail: MailServer
) -> None:
    db.responses[READ_QUEUE] = [queue_row(11)]
    db.responses[INFO] = [
        {"token": "T", "employee_fio": "К", "employee_id": 9, "employee_type": "CLIENT"}
    ]

    await client.get(ROUTE)

    assert db.calls_of(TOKEN) == [(0, 9, 11)]
    assert len(mail.letters) == 1


@pytest.mark.parametrize("token", [None, ""])
async def test_no_remont_token_means_no_button(
    client: AsyncClient, db: Database, mail: MailServer, token: str | None
) -> None:
    db.responses[READ_QUEUE] = [queue_row(11)]
    db.responses[TOKEN] = [{"id": token}]

    await client.get(ROUTE)

    assert "redirect-email" not in _html(mail.letters[0].content)


async def test_expeditor_letter_gets_the_copy(
    client: AsyncClient, db: Database, mail: MailServer
) -> None:
    db.responses[READ_QUEUE] = [queue_row(11, what="EXPEDITOR_CODE_ACCEPT")]

    await client.get(ROUTE)

    letter = mail.letters[0]
    assert letter.recipients == ("one@test.invalid", CC_ADDRESS)
    assert b"Cc: =?utf8?Q?" in letter.content


async def test_each_address_gets_its_own_letter_on_one_connection(
    client: AsyncClient, db: Database, mail: MailServer
) -> None:
    db.responses[READ_QUEUE] = [queue_row(11, email="a@test.invalid; b@test.invalid ")]

    await client.get(ROUTE)

    assert [letter.recipients for letter in mail.letters] == [
        ("a@test.invalid",),
        ("b@test.invalid",),
    ]
    assert db.calls_of(CHECK) == [("a@test.invalid",), (" b@test.invalid ",)]
    assert db.calls_of(MARK) == [(11, 1), (11, 1)]
    assert len(mail.settings) == 1


async def test_unsubscribed_address_is_skipped_and_the_row_is_not_touched(
    client: AsyncClient, db: Database, mail: MailServer
) -> None:
    db.responses[READ_QUEUE] = [queue_row(11)]
    db.responses[CHECK] = [{"id": 0}]

    await client.get(ROUTE)

    assert mail.letters == []
    assert db.calls_of(MARK) == []
    assert db.calls_of(FAILED) == []


async def test_row_is_marked_before_the_letter_and_no_transaction_stays_open(
    client: AsyncClient, db: Database, mail: MailServer
) -> None:
    db.responses[READ_QUEUE] = [queue_row(11)]

    await client.get(ROUTE)

    assert db.events.index(MARK) < db.events.index("smtp")
    assert db.events[db.events.index(MARK) + 1] == "commit"
    assert mail.transactions_during_sending == [0]


async def test_failed_mark_stops_the_run_and_nothing_goes(
    client: AsyncClient, db: Database, mail: MailServer
) -> None:
    db.responses[READ_QUEUE] = [queue_row(11), queue_row(12)]
    db.responses[MARK] = RaiseError("Отказ")

    response = await client.get(ROUTE)

    assert response.status_code == 200
    assert mail.attempts == 0
    assert db.calls_of(MARK) == [(11, 1)]
    assert db.calls_of(FAILED) == []
    assert mail.closed == 1


async def test_lost_database_at_the_read_is_an_error_the_cron_sees(
    client: AsyncClient, db: Database, mail: MailServer
) -> None:
    db.responses[READ_QUEUE] = InterfaceError("connection closed")

    with pytest.raises(DatabaseGoneError):
        await client.get(ROUTE)

    assert mail.settings == []


async def test_lost_database_stops_the_run(
    client: AsyncClient, db: Database, mail: MailServer
) -> None:
    db.responses[READ_QUEUE] = [queue_row(11), queue_row(12)]
    db.responses[READ_ATTACHMENTS] = InterfaceError("connection closed")

    response = await client.get(ROUTE)

    assert response.status_code == 200
    assert mail.attempts == 0
    assert mail.closed == 1


async def test_refusal_in_the_middle_goes_on_records_it_and_prints_it(
    client: AsyncClient, db: Database, mail: MailServer
) -> None:
    db.responses[READ_QUEUE] = [queue_row(11), queue_row(12), queue_row(13)]
    mail.refuse[2] = SmtpError("5.1.1 refused\r\n", 550)

    response = await client.get(ROUTE)

    assert response.status_code == 200
    assert response.content == b"5.1.1 refused\r\n"
    assert mail.attempts == 3
    assert db.calls_of(MARK) == [(11, 1), (12, 1), (13, 1)]
    assert db.calls_of(FAILED) == [(12, 2, "5.1.1 refused\r\n")]
    assert db.calls_of(HARD) == []
    # The one connection of the run, also after the refusal: PHP never replaces it.
    assert len(mail.settings) == 1


async def test_no_reply_in_time_records_the_row_and_leaves_the_rest_queued(
    client: AsyncClient, db: Database, mail: MailServer
) -> None:
    db.responses[READ_QUEUE] = [queue_row(11), queue_row(12), queue_row(13)]
    mail.refuse[2] = SmtpTimeoutError("smtp.test.invalid has timed out")

    response = await client.get(ROUTE)

    assert response.status_code == 200
    assert response.content == b"smtp.test.invalid has timed out"
    assert mail.attempts == 2
    assert db.calls_of(MARK) == [(11, 1), (12, 1)]
    assert db.calls_of(FAILED) == [(12, 2, "smtp.test.invalid has timed out")]
    assert mail.closed == 1


async def test_log_tells_a_sent_row_from_one_only_marked(
    client: AsyncClient, db: Database, mail: MailServer, log_lines: list[str]
) -> None:
    db.responses[READ_QUEUE] = [queue_row(11), queue_row(12)]
    mail.refuse[2] = SmtpError("no\r\n", 550)

    await client.get(ROUTE)

    logged = "".join(log_lines)
    assert "send_email 11: marked" in logged
    assert "send_email 11: sent" in logged
    assert "send_email 12: marked" in logged
    assert "send_email 12: sent" not in logged


async def test_code_504_unsubscribes_the_trimmed_address(
    client: AsyncClient, db: Database, mail: MailServer
) -> None:
    db.responses[READ_QUEUE] = [queue_row(11, email=" a@test.invalid ")]
    mail.refuse[1] = SmtpError("5.5.4 x\r\n", 504)

    await client.get(ROUTE)

    assert db.calls_of(HARD) == [("a@test.invalid",)]
    assert db.events.index(HARD) < db.events.index(FAILED)


async def test_failed_record_of_a_refusal_does_not_stop_the_queue(
    client: AsyncClient, db: Database, mail: MailServer, log_lines: list[str]
) -> None:
    db.responses[READ_QUEUE] = [queue_row(11), queue_row(12)]
    db.responses[FAILED] = RaiseError("Отказ")
    mail.refuse[1] = SmtpError("no\r\n", 550)

    response = await client.get(ROUTE)

    assert response.content == b"no\r\n"
    assert mail.attempts == 2
    assert "upd_email_send_failed" in "".join(log_lines)


async def test_too_long_subject_is_recorded_as_php_does(
    client: AsyncClient, db: Database, mail: MailServer
) -> None:
    db.responses[READ_QUEUE] = [queue_row(11, email_title="x" * 1200)]

    response = await client.get(ROUTE)

    text = "At least one mail header line is too long"
    assert response.content == text.encode()
    assert mail.attempts == 0
    assert db.calls_of(MARK) == []
    assert db.calls_of(FAILED) == [(11, 2, text)]


@pytest.mark.parametrize(
    ("function", "letters"),
    [
        (READ_QUEUE, 0),
        (READ_ATTACHMENTS, 1),
        (CHECK, 0),
        (INFO, 1),
        (TOKEN, 1),
    ],
)
async def test_failed_read_counts_as_an_empty_result(
    client: AsyncClient,
    db: Database,
    mail: MailServer,
    function: str,
    letters: int,
) -> None:
    db.responses[READ_QUEUE] = [queue_row(11)]
    db.responses[function] = RaiseError("Отказ")

    response = await client.get(ROUTE)

    assert response.status_code == 200
    assert response.content == b""
    assert len(mail.letters) == letters


async def test_failed_unsubscribe_info_gives_a_letter_without_token_and_button(
    client: AsyncClient, db: Database, mail: MailServer
) -> None:
    db.responses[READ_QUEUE] = [queue_row(11)]
    db.responses[INFO] = RaiseError("Отказ")

    await client.get(ROUTE)

    message = _parse(mail.letters[0].content)
    assert message["List-Unsubscribe"] == "https://office.test/index/unsubscribe?token="
    assert "redirect-email" not in _html(mail.letters[0].content)
    assert db.calls_of(TOKEN) == []


async def test_attachment_is_read_from_the_office_folder(
    client: AsyncClient,
    db: Database,
    mail: MailServer,
    tmp_path: Path,
) -> None:
    folder = tmp_path / "documents"
    folder.mkdir()
    (folder / "a.pdf").write_bytes(b"%PDF contour")
    db.responses[READ_QUEUE] = [queue_row(11)]
    db.responses[READ_ATTACHMENTS] = [{"file_url": "/documents/a.pdf", "file_name": "Файл.pdf"}]

    await client.get(ROUTE)

    parts = list(_parse(mail.letters[0].content).walk())
    attachment = parts[-1]
    assert attachment.get_content_type() == "application/pdf"
    assert attachment.get_payload(decode=True) == b"%PDF contour"
    assert 'filename="Файл.pdf"'.encode() in mail.letters[0].content


async def test_missing_attachment_gives_an_empty_part_as_php(
    client: AsyncClient,
    db: Database,
    mail: MailServer,
) -> None:
    db.responses[READ_QUEUE] = [queue_row(11)]
    db.responses[READ_ATTACHMENTS] = [{"file_url": "/documents/none.pdf", "file_name": "Нет.pdf"}]

    await client.get(ROUTE)

    attachment = list(_parse(mail.letters[0].content).walk())[-1]
    assert attachment.get_payload(decode=True) == b""


async def test_path_outside_the_office_folder_is_not_attached(
    client: AsyncClient,
    db: Database,
    mail: MailServer,
    tmp_path: Path,
) -> None:
    secret = tmp_path.parent / "outside.txt"
    secret.write_bytes(b"secret")
    db.responses[READ_QUEUE] = [queue_row(11)]
    db.responses[READ_ATTACHMENTS] = [{"file_url": "/../outside.txt", "file_name": "x.pdf"}]

    await client.get(ROUTE)

    assert b"c2VjcmV0" not in mail.letters[0].content


async def test_overlapping_run_sends_nothing(
    client: AsyncClient, db: Database, mail: MailServer
) -> None:
    db.lock_is_held = True
    db.responses[READ_QUEUE] = [queue_row(11)]

    response = await client.get(ROUTE)

    assert response.status_code == 200
    assert db.calls_of(READ_QUEUE) == []
    assert mail.settings == []
    assert len(db.calls_of(LOCK)) == 1


async def test_no_address_and_no_text_go_to_the_log(
    client: AsyncClient, db: Database, mail: MailServer, log_lines: list[str]
) -> None:
    db.responses[READ_QUEUE] = [queue_row(11, email="secret-address@test.invalid")]
    db.responses[READ_ATTACHMENTS] = [{"file_url": "/documents/none.pdf", "file_name": "Нет.pdf"}]
    mail.refuse[1] = SmtpError("refused secret-address@test.invalid\r\n", 550)

    await client.get(ROUTE)

    logged = "".join(log_lines)
    assert "secret-address" not in logged
    assert "<p>Текст</p>" not in logged
