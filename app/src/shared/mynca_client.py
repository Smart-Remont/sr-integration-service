"""MyNCA as PHP ``MyNcaClient`` calls it, for the routes moved from PHP.

Unlike the factoring client it follows no redirect and does not raise: failures are values, as PHP
returned them. No method logs a request or an answer: they can carry a company key and its password,
a document or a signer's IIN.
"""

import base64
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from urllib.parse import quote

import httpx
from loguru import logger

from src.php_compat.json_codec import php_json_encode_unescaped
from src.php_compat.text import php_string

_USER_AGENT = "smremont-mynca-client/1.0"
_MYNCA_TIMEOUT = httpx.Timeout(timeout=30.0, connect=10.0)
# Far above PHP's 30 s on purpose. MyNCA stamps the signature (TSP) and saves it while we wait, and an
# answer lost here leaves a signature nobody recorded: the next cron run signs again. The run lock
# keeps other runs out. The longer wait has a price: a deploy can kill the worker during it, which
# leaves the same unrecorded signature.
_SIGN_SAVE_TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0)
_OK_FIRST = 200
_OK_LAST = 299
_SIGN_PROCESS_PATH = re.compile(r"^/sign/[^/]+/(.+)$")


class _Mode(StrEnum):
    """Binary answers still decode as JSON when MyNCA reports an error that way."""

    JSON = "json"
    AUTO = "auto"
    BINARY = "binary"


@dataclass(frozen=True, repr=False)
class MyncaAnswer:
    """The array ``MyNcaClient::request`` returns. It holds documents: never log it."""

    status: bool
    code: int
    data: Any = None
    raw: bytes | None = None
    error: str = ""
    is_json: bool = False

    def __repr__(self) -> str:
        return f"MyncaAnswer(status={self.status}, code={self.code})"

    @property
    def has_file(self) -> bool:
        """The opposite of PHP ``empty($answer['raw'])``, which takes "0" for empty too."""
        return self.raw not in (None, b"", b"0")

    def field(self, name: str) -> Any:
        return self.data.get(name) if isinstance(self.data, dict) else None


class MyncaClient:
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

    async def pkcs12_info(self, payload: dict[str, object]) -> MyncaAnswer:
        return await self._request("POST", "/pkcs12/info", payload, _Mode.JSON)

    async def sign_get_file(self, sign_process_id: str) -> MyncaAnswer:
        path = f"/sign/{_encoded(sign_process_id)}/file"
        return await self._request("GET", path, mode=_Mode.JSON)

    async def cms_sign_save(self, payload: dict[str, object]) -> MyncaAnswer:
        return await self._request(
            "POST",
            "/cms/sign-save",
            payload,
            _Mode.JSON,
            time_limits=_SIGN_SAVE_TIMEOUT,
        )

    async def sign_rollback(self, sign_process_id: str) -> MyncaAnswer:
        path = f"/sign/{_encoded(sign_process_id)}/rollback"
        return await self._request("POST", path, mode=_Mode.JSON)

    async def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        mode: _Mode = _Mode.BINARY,
        time_limits: httpx.Timeout = _MYNCA_TIMEOUT,
    ) -> MyncaAnswer:
        try:
            async with httpx.AsyncClient(
                timeout=time_limits,
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
    # The group id opens a signed document, so it does not go to the log, and neither does a process.
    if path.startswith("/sign/group/"):
        return "/sign/group/*"
    return _SIGN_PROCESS_PATH.sub(r"/sign/*/\1", path)
