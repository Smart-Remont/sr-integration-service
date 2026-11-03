"""The protocol of ``SmtpTransport``: what goes out and what a failure says. No server is reached."""

import asyncio
import base64
import ssl

import pytest

from src.features.mailing.message import ComposedLetter
from src.features.mailing.smtp import (
    SmtpError,
    SmtpSessionError,
    SmtpSettings,
    SmtpTimeoutError,
    SmtpTransport,
)
from tests.mailing.fakes import StubWriter

SETTINGS = SmtpSettings("smtp.test.invalid", 587, "user@test.invalid", "secret-pass")
LETTER = ComposedLetter(
    return_path="from@test.invalid",
    recipients=("a@test.invalid", "b@test.invalid"),
    content=b"Subject: x\n\nline\n.dot\n..two",
)
HELLO = [
    b"220 hi\r\n",
    b"250-smtp\r\n250 STARTTLS\r\n",
    b"220 go\r\n",
    b"250 AUTH LOGIN\r\n",
    b"334 VXNlcm5hbWU6\r\n",
    b"334 UGFzc3dvcmQ6\r\n",
    b"235 ok\r\n",
]
LETTER_REPLIES = [b"250 s\r\n", b"250 r\r\n", b"250 r\r\n", b"354 go\r\n", b"250 queued\r\n"]


class Connected:
    """A transport with scripted replies in place of a socket."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, replies: list[bytes]) -> None:
        self.reader = asyncio.StreamReader()
        for reply in replies:
            self.reader.feed_data(reply)
        self.writer = StubWriter()
        self.tls: list[str] = []
        self.transport = SmtpTransport(SETTINGS)

        async def connect(host: str, port: int) -> tuple[asyncio.StreamReader, StubWriter]:
            self.tls.append(f"connect {host}:{port}")
            return self.reader, self.writer

        async def start_tls(transport: SmtpTransport) -> None:
            self.tls.append("start_tls")
            transport._tls_started = True

        monkeypatch.setattr(asyncio, "open_connection", connect)
        monkeypatch.setattr(SmtpTransport, "_start_tls", start_tls)

    def commands(self) -> list[bytes]:
        return bytes(self.writer.written).split(b"\r\n")


async def test_session_connects_starts_tls_logs_in_and_the_first_letter_has_no_reset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connected = Connected(monkeypatch, [*HELLO, *LETTER_REPLIES])

    await connected.transport.open_session()
    await connected.transport.send(LETTER)

    assert connected.tls == ["connect smtp.test.invalid:587", "start_tls"]
    assert connected.commands() == [
        b"EHLO localhost",
        b"STARTTLS",
        b"EHLO localhost",
        b"AUTH LOGIN",
        base64.b64encode(SETTINGS.username.encode()),
        base64.b64encode(SETTINGS.password.encode()),
        b"MAIL FROM:<from@test.invalid>",
        b"RCPT TO:<a@test.invalid>",
        b"RCPT TO:<b@test.invalid>",
        b"DATA",
        b"Subject: x",
        b"",
        b"line",
        b"..dot",
        b"...two",
        b".",
        b"",
    ]


async def test_second_letter_stays_on_the_connection_with_reset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connected = Connected(monkeypatch, [*HELLO, *LETTER_REPLIES, b"250 reset\r\n", *LETTER_REPLIES])

    await connected.transport.open_session()
    await connected.transport.send(LETTER)
    await connected.transport.send(LETTER)

    assert connected.commands().count(b"RSET") == 1
    assert len(connected.tls) == 2


@pytest.mark.parametrize(
    ("reply", "message", "code"),
    [
        (b"550 5.1.1 refused\r\n", "5.1.1 refused\r\n", 550),
        (b"550-first\r\n550-second\r\n550 third\r\n", "first\r\n second\r\n third\r\n", 550),
        (b"504 x\r\n", "x\r\n", 504),
        (b"garbage line\r\n", "line\r\n", 0),
    ],
)
async def test_refusal_says_what_php_stores(
    monkeypatch: pytest.MonkeyPatch, reply: bytes, message: str, code: int
) -> None:
    connected = Connected(monkeypatch, [*HELLO, b"250 s\r\n", reply])
    await connected.transport.open_session()

    with pytest.raises(SmtpError) as failure:
        await connected.transport.send(LETTER)

    assert failure.value.message == message
    assert failure.value.code == code


async def test_refusal_without_text_counts_as_accepted_as_in_zend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connected = Connected(
        monkeypatch, [*HELLO, *LETTER_REPLIES[:1], b"550\r\n", *LETTER_REPLIES[2:]]
    )

    await connected.transport.open_session()
    await connected.transport.send(LETTER)

    assert connected.commands()[-2] == b"."


async def test_lost_connection_stays_lost_for_the_rest_of_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connected = Connected(monkeypatch, [*HELLO, b"250 s\r\n"])
    connected.reader.feed_eof()
    await connected.transport.open_session()

    with pytest.raises(SmtpError, match=r"Could not read from smtp\.test\.invalid"):
        await connected.transport.send(LETTER)
    with pytest.raises(SmtpError, match=r"Could not read from smtp\.test\.invalid"):
        await connected.transport.send(LETTER)


async def test_write_failure_over_tls_reads_as_php_and_before_tls_says_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BrokenWriter(StubWriter):
        async def drain(self) -> None:
            raise ConnectionResetError

    connected = Connected(monkeypatch, [*HELLO, *LETTER_REPLIES])
    await connected.transport.open_session()
    await connected.transport.send(LETTER)
    connected.transport._writer = BrokenWriter()
    connected.reader.feed_eof()
    connected.transport._tls_started = True
    with pytest.raises(SmtpError, match=r"Could not read from"):
        await connected.transport.send(LETTER)

    connected.transport._tls_started = False
    with pytest.raises(SmtpError, match=r"Could not send request to"):
        await connected.transport.send(LETTER)


async def test_refused_connection_is_a_failed_session(monkeypatch: pytest.MonkeyPatch) -> None:
    async def refused(*_: object) -> None:
        raise ConnectionRefusedError(111, "Connection refused")

    monkeypatch.setattr(asyncio, "open_connection", refused)

    with pytest.raises(SmtpSessionError, match="Connection refused"):
        await SmtpTransport(SETTINGS).open_session()


@pytest.mark.parametrize(
    ("replies", "message", "code"),
    [
        ([b"554 go away\r\n"], "go away\r\n", 554),
        ([*HELLO[:2], b"454 no tls\r\n"], "no tls\r\n", 454),
        ([*HELLO[:6], b"535 bad\r\n"], "bad\r\n", 535),
    ],
    ids=["greeting", "starttls", "login"],
)
async def test_refusal_before_the_first_letter_is_a_failed_session(
    monkeypatch: pytest.MonkeyPatch, replies: list[bytes], message: str, code: int
) -> None:
    connected = Connected(monkeypatch, replies)

    with pytest.raises(SmtpSessionError) as failure:
        await connected.transport.open_session()

    assert failure.value.message == message
    assert failure.value.code == code


async def test_tls_failure_is_a_failed_session(monkeypatch: pytest.MonkeyPatch) -> None:
    connected = Connected(monkeypatch, HELLO[:3])

    async def broken_tls(_: SmtpTransport) -> None:
        raise SmtpError("Unable to connect via TLS")

    monkeypatch.setattr(SmtpTransport, "_start_tls", broken_tls)

    with pytest.raises(SmtpSessionError, match="Unable to connect via TLS"):
        await connected.transport.open_session()


async def test_silent_server_before_the_first_letter_is_a_failed_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connected = Connected(monkeypatch, [])
    monkeypatch.setattr("src.features.mailing.smtp._COMMAND_SECONDS", 0.01)

    with pytest.raises(SmtpSessionError, match=r"smtp\.test\.invalid has timed out"):
        await connected.transport.open_session()


async def test_ehlo_refused_falls_back_to_helo(monkeypatch: pytest.MonkeyPatch) -> None:
    connected = Connected(
        monkeypatch,
        [
            b"220 hi\r\n",
            b"500 no\r\n",
            b"250 ok\r\n",
            b"220 go\r\n",
            b"250 ok\r\n",
            *HELLO[4:],
            *LETTER_REPLIES,
        ],
    )

    await connected.transport.open_session()
    await connected.transport.send(LETTER)

    assert connected.commands()[:3] == [b"EHLO localhost", b"HELO localhost", b"STARTTLS"]


class TlsWriter(StubWriter):
    def __init__(self) -> None:
        super().__init__()
        self.tls: tuple[ssl.SSLContext, str | None] | None = None

    async def start_tls(self, context: ssl.SSLContext, *, server_hostname: str | None) -> None:
        self.tls = (context, server_hostname)


def _transport_before_tls(writer: StubWriter, unread: bytes = b"") -> SmtpTransport:
    transport = SmtpTransport(SETTINGS)
    transport._reader = asyncio.StreamReader()
    transport._reader.feed_data(unread)
    transport._writer = writer  # type: ignore[assignment]
    return transport


async def test_tls_checks_the_certificate_and_the_host_name() -> None:
    writer = TlsWriter()

    await _transport_before_tls(writer)._start_tls()

    assert writer.tls is not None
    context, host = writer.tls
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert host == SETTINGS.host


async def test_lines_sent_before_tls_do_not_pass_for_replies_after_it() -> None:
    writer = TlsWriter()

    with pytest.raises(SmtpError, match="Unable to connect via TLS"):
        await _transport_before_tls(writer, b"235 forged\r\n")._start_tls()

    assert writer.tls is None


async def test_no_reply_in_time_is_its_own_error(monkeypatch: pytest.MonkeyPatch) -> None:
    connected = Connected(monkeypatch, [*HELLO])
    await connected.transport.open_session()
    monkeypatch.setattr("src.features.mailing.smtp._COMMAND_SECONDS", 0.01)

    with pytest.raises(SmtpTimeoutError, match=r"smtp\.test\.invalid has timed out"):
        await connected.transport.send(LETTER)


async def test_close_sends_quit_once_and_ignores_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connected = Connected(monkeypatch, [*HELLO, *LETTER_REPLIES, b"500 no\r\n"])

    await connected.transport.open_session()
    await connected.transport.send(LETTER)
    await connected.transport.close()

    assert connected.commands().count(b"QUIT") == 1
    assert connected.writer.closed is True


async def test_close_without_a_connection_does_nothing() -> None:
    await SmtpTransport(SETTINGS).close()
