"""SMTP as ``Zend_Mail_Protocol_Smtp`` speaks it: STARTTLS, AUTH LOGIN, one connection for a run.

The queue tables keep the text of a refusal and the answer of the cron prints it, so the failures have
to be the same too. ``smtplib`` hides them: it strips the reply and has its own errors.

One connection stays for the whole queue, also after a failure, as in PHP (``send_email`` replaces its
own copy of the transport and the caller never sees it). When the server has gone, the rest of the
queue therefore fails with the same kind of error. See PHP_DEFECTS.md.

TLS is verified, as PHP does by default. No letter text or address goes to the log from here.
"""

import asyncio
import base64
import contextlib
import re
import ssl
from collections.abc import Iterable
from dataclasses import dataclass
from socket import gaierror

from .message import ComposedLetter

_HELO_NAME = b"localhost"
_CONNECT_SECONDS = 30.0
# RFC 5321 and Zend allow minutes. A server that hangs must not keep the queue for hours, and the
# next run of the cron finds the lock anyway. The end of DATA takes longest: the server is checking.
_COMMAND_SECONDS = 60.0
_DATA_END_SECONDS = 120.0
_CLOSE_SECONDS = 2.0
_REPLY_SPLIT = re.compile(rb"([\s-]+)")
_LINE = b"\r\n"


@dataclass(frozen=True)
class SmtpSettings:
    host: str
    port: int
    username: str
    password: str


class SmtpError(Exception):
    """``Zend_Mail_Protocol_Exception``. ``code`` is the reply code, 0 without one."""

    def __init__(self, message: str, code: int = 0) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class SmtpTimeoutError(SmtpError):
    """No reply in time. A late reply would then answer the next command, so the run has to stop."""


class SmtpTransport:
    def __init__(self, settings: SmtpSettings) -> None:
        self._settings = settings
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._has_connection = False
        self._tls_started = False
        self._in_session = False
        self._mail_given = False
        self._recipient_given = False

    async def send(self, letter: ComposedLetter) -> None:
        if self._has_connection:
            await self._reset()
        else:
            # Zend keeps the connection object even when connecting fails: the next letter does
            # not connect again and gets "No connection has been established".
            self._has_connection = True
            await self._connect()
            await self._helo()
        await self._mail(letter.return_path)
        for recipient in letter.recipients:
            await self._recipient(recipient)
        await self._data(letter.content)

    async def close(self) -> None:
        if self._in_session:
            with contextlib.suppress(SmtpError):
                await self._send(b"QUIT")
                await self._expect((221,), _CLOSE_SECONDS)
            self._in_session = False
        if self._writer is not None:
            self._writer.close()
            with contextlib.suppress(OSError, ssl.SSLError, TimeoutError):
                await asyncio.wait_for(self._writer.wait_closed(), _CLOSE_SECONDS)

    async def _connect(self) -> None:
        settings = self._settings
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(settings.host, settings.port),
                _CONNECT_SECONDS,
            )
        except gaierror as exc:
            raise SmtpError("Could not open socket") from exc
        except TimeoutError as exc:
            raise SmtpError("Connection timed out") from exc
        except OSError as exc:
            raise SmtpError(exc.strerror or "Could not open socket") from exc

    async def _helo(self) -> None:
        await self._expect((220,))
        await self._ehlo()
        await self._send(b"STARTTLS")
        await self._expect((220,))
        await self._start_tls()
        await self._ehlo()
        self._in_session = True
        await self._login()

    async def _ehlo(self) -> None:
        try:
            await self._send(b"EHLO " + _HELO_NAME)
            await self._expect((250,))
        except SmtpError:
            await self._send(b"HELO " + _HELO_NAME)
            await self._expect((250,))

    async def _start_tls(self) -> None:
        if self._writer is None or await self._has_unread_data():
            raise SmtpError("Unable to connect via TLS")
        try:
            await asyncio.wait_for(
                self._writer.start_tls(
                    ssl.create_default_context(),
                    server_hostname=self._settings.host,
                ),
                _CONNECT_SECONDS,
            )
        except (OSError, ssl.SSLError, TimeoutError) as exc:
            raise SmtpError("Unable to connect via TLS") from exc
        self._tls_started = True

    async def _has_unread_data(self) -> bool:
        """Lines sent before TLS must not pass for replies after it: someone on the way can add them."""
        if self._reader is None:
            return False
        # A limit of 0 ends at the first wait, and a line already in the buffer takes no wait.
        try:
            async with asyncio.timeout(0):
                return bool(await self._reader.read(1))
        except TimeoutError:
            return False

    async def _login(self) -> None:
        settings = self._settings
        await self._send(b"AUTH LOGIN")
        await self._expect((334,))
        await self._send(base64.b64encode(settings.username.encode()))
        await self._expect((334,))
        await self._send(base64.b64encode(settings.password.encode()))
        await self._expect((235,))

    async def _reset(self) -> None:
        await self._send(b"RSET")
        await self._expect((250, 220))
        self._mail_given = False
        self._recipient_given = False

    async def _mail(self, sender: str) -> None:
        if not self._in_session:
            raise SmtpError("A valid session has not been started")
        await self._send(b"MAIL FROM:<" + sender.encode("utf-8", "surrogateescape") + b">")
        await self._expect((250,))
        self._mail_given = True
        self._recipient_given = False

    async def _recipient(self, recipient: str) -> None:
        if not self._mail_given:
            raise SmtpError("No sender reverse path has been supplied")
        await self._send(b"RCPT TO:<" + recipient.encode("utf-8", "surrogateescape") + b">")
        await self._expect((250, 251))
        self._recipient_given = True

    async def _data(self, content: bytes) -> None:
        if not self._recipient_given:
            raise SmtpError("No recipient forward path has been supplied")
        await self._send(b"DATA")
        await self._expect((354,))
        await self._send(_LINE.join(_dot_stuffed(content.split(b"\n"))) + _LINE + b".")
        await self._expect((250,), _DATA_END_SECONDS)

    async def _send(self, request: bytes) -> None:
        if self._writer is None:
            raise SmtpError(f"No connection has been established to {self._settings.host}")
        try:
            self._writer.write(request + _LINE)
            await asyncio.wait_for(self._writer.drain(), _COMMAND_SECONDS)
        except TimeoutError as exc:
            raise SmtpTimeoutError(f"{self._settings.host} has timed out") from exc
        except (OSError, ssl.SSLError) as exc:
            # Over TLS PHP ``fwrite`` gives 0 for a failed write, not false: Zend goes on to read, and
            # the error of the letter is the one of the read.
            if not self._tls_started:
                raise SmtpError(f"Could not send request to {self._settings.host}") from exc

    async def _receive(self, seconds: float) -> bytes:
        if self._reader is None:
            raise SmtpError(f"No connection has been established to {self._settings.host}")
        try:
            line = await asyncio.wait_for(self._reader.readline(), seconds)
        except TimeoutError as exc:
            raise SmtpTimeoutError(f"{self._settings.host} has timed out") from exc
        except (OSError, ssl.SSLError, ValueError) as exc:
            raise SmtpError(f"Could not read from {self._settings.host}") from exc
        if not line:
            raise SmtpError(f"Could not read from {self._settings.host}")
        return line

    async def _expect(self, codes: tuple[int, ...], seconds: float | None = None) -> None:
        """``_expect``: a line ``code-text`` goes on, and the error keeps the line end of the reply."""
        error = b""
        command = b""
        while True:
            line = await self._receive(_COMMAND_SECONDS if seconds is None else seconds)
            pieces = _REPLY_SPLIT.split(line, maxsplit=1)
            command = pieces[0]
            more = pieces[1] if len(pieces) > 1 else b""
            text = pieces[2] if len(pieces) > 2 else b""
            if error:
                error += b" " + text
            elif not command.isdigit() or int(command) not in codes:
                error = text
            if not more.startswith(b"-"):
                break
        if error:
            code = int(command) if command.isdigit() else 0
            raise SmtpError(error.decode("utf-8", "replace"), code)


def _dot_stuffed(lines: Iterable[bytes]) -> Iterable[bytes]:
    return (b"." + line if line.startswith(b".") else line for line in lines)
