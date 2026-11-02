"""External HTTP of the signing routes: MyNCA, the partner backend and document addresses."""

import base64
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from urllib.parse import quote

import httpx
from loguru import logger

from src.shared.php_json import php_json_encode_unescaped
from src.shared.php_text import php_string

_USER_AGENT = "smremont-mynca-client/1.0"
_MYNCA_TIMEOUT = httpx.Timeout(timeout=30.0, connect=10.0)
# PHP waited on these without its own limit, and a document is built while the caller waits:
# file_get_contents stopped at default_socket_timeout, 60 s, and curl never did.
_DOCUMENT_TIMEOUT = httpx.Timeout(timeout=60.0, connect=10.0)
_OK_FIRST = 200
_OK_LAST = 299
_FIRST_HTTP_ERROR = 400


class _Mode(StrEnum):
    """Binary answers still decode as JSON when MyNCA reports an error that way."""

    JSON = "json"
    AUTO = "auto"
    BINARY = "binary"


@dataclass(frozen=True)
class MyncaAnswer:
    """The array ``MyNcaClient::request`` returns. Failures are values, as the routes expect them."""

    status: bool
    code: int
    data: Any = None
    raw: bytes | None = None
    error: str = ""
    is_json: bool = False

    def field(self, name: str) -> Any:
        return self.data.get(name) if isinstance(self.data, dict) else None


class MyncaClient:
    """PHP ``MyNcaClient``. Unlike the factoring client it follows no redirect and does not raise."""

    def __init__(
        self,
        base_url: str,
        token: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._transport = transport

    async def docx_to_pdf(self, docx: bytes) -> MyncaAnswer:
        payload = {"docx": base64.b64encode(docx).decode("ascii"), "is_file": False}
        return await self._request("POST", "/document/docx-to-pdf", payload, _Mode.AUTO)

    async def sign_create(self, payload: dict[str, object]) -> MyncaAnswer:
        return await self._request("POST", "/sign/create", payload, _Mode.JSON)

    async def group_download_pdf(self, group_id: str) -> MyncaAnswer:
        return await self._request("GET", f"/sign/group/{_encoded(group_id)}/download")

    async def group_download_cms(self, group_id: str) -> MyncaAnswer:
        return await self._request("GET", f"/sign/group/{_encoded(group_id)}/download-cms")

    async def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        mode: _Mode = _Mode.BINARY,
    ) -> MyncaAnswer:
        try:
            async with httpx.AsyncClient(
                timeout=_MYNCA_TIMEOUT,
                follow_redirects=False,
                transport=self._transport,
            ) as client:
                response = await client.request(
                    method,
                    f"{self._base_url}{path}",
                    headers=self._headers(mode, has_body=payload is not None),
                    content=None if payload is None else _json_body(payload),
                )
        except httpx.RequestError as exc:
            logger.error(
                "MyNCA transport error | path={path} error={error}",
                path=_logged_path(path),
                error=type(exc).__name__,
            )
            return MyncaAnswer(status=False, code=0, error=str(exc) or type(exc).__name__)
        logger.info(
            "MyNCA HTTP | method={method} path={path} status={status}",
            method=method,
            path=_logged_path(path),
            status=response.status_code,
        )
        return _answer(response, mode)

    def _headers(self, mode: _Mode, *, has_body: bool) -> dict[str, str]:
        headers = {
            "User-Agent": _USER_AGENT,
            "Accept": "application/json" if mode == _Mode.JSON else "*/*",
            "Authorization": f"Bearer {self._token}",
        }
        if has_body:
            headers["Content-Type"] = "application/json"
        return headers


class DocumentFetcher:
    """Plain downloads of documents, without the credentials of MyNCA."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    async def file_get_contents(self, url: str) -> bytes | None:
        """``@file_get_contents``: follows redirects, and an HTTP error is a failure."""
        try:
            async with httpx.AsyncClient(
                timeout=_DOCUMENT_TIMEOUT,
                follow_redirects=True,
                transport=self._transport,
            ) as client:
                response = await client.get(url)
        except httpx.RequestError as exc:
            logger.warning("Document fetch failed | error={error}", error=type(exc).__name__)
            return None
        if response.status_code >= _FIRST_HTTP_ERROR:
            logger.warning("Document fetch refused | status={status}", status=response.status_code)
            return None
        return response.content

    async def partner_signed_file(self, url: str) -> bytes:
        """``getSignedFile``: any answer of the partner becomes the file, a failure an empty one."""
        try:
            async with httpx.AsyncClient(
                timeout=_DOCUMENT_TIMEOUT,
                follow_redirects=False,
                transport=self._transport,
            ) as client:
                response = await client.get(url, headers={"Content-Type": "application/json"})
        except httpx.RequestError as exc:
            logger.warning("Signed file fetch failed | error={error}", error=type(exc).__name__)
            return b""
        logger.info("Signed file fetch | status={status}", status=response.status_code)
        return response.content


def _answer(response: httpx.Response, mode: _Mode) -> MyncaAnswer:
    ok = _OK_FIRST <= response.status_code <= _OK_LAST
    raw = response.content
    is_json_type = "json" in response.headers.get("content-type", "").lower()
    if mode != _Mode.JSON and not is_json_type:
        return MyncaAnswer(status=ok, code=response.status_code, data=raw, raw=raw)
    try:
        data = json.loads(raw)
    except ValueError:
        return MyncaAnswer(
            status=ok,
            code=response.status_code,
            raw=raw,
            error="json_decode_error: Syntax error",
        )
    return MyncaAnswer(
        status=ok,
        code=response.status_code,
        data=data,
        raw=raw,
        error=_error_text(data) if not ok else "",
        is_json=True,
    )


def _error_text(data: object) -> str:
    if not isinstance(data, dict):
        return ""
    for name in ("error", "message"):
        if data.get(name) is not None:
            return php_string(data[name])
    return ""


def _json_body(payload: dict[str, object]) -> bytes:
    return php_json_encode_unescaped(payload).encode("utf-8", "surrogateescape")


def _encoded(value: str) -> str:
    """``rawurlencode``."""
    return quote(value, safe="-_.~")


def _logged_path(path: str) -> str:
    # The group id opens a signed document, so it does not go to the log.
    return "/sign/group/*" if path.startswith("/sign/group/") else path
