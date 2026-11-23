"""HTTP to MySpace ``/ext/v1``: the token exchange and the reads. No database.

MySpace decides everything about access itself: whether the caller's token is alive, whether this
gateway serves its audience, the scopes, the employee and his rights. Here only the transport and
the mapping of its answers to exceptions.
"""

import base64
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx
from loguru import logger

EXCHANGE_PATH = "/ext/v1/auth/token/"
EXCHANGE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:token-exchange"
ACCESS_SUBJECT_TYPE = "urn:ietf:params:oauth:token-type:access_token"
# The audience of the token the gateway reads MySpace with.
DATA_AUDIENCE = "myspace-ext"

# MySpace runs each read under a 3 s statement timeout; the rest is the network and Django.
# A whole request (exchange, read, a retry) also has a deadline: session.CALL_DEADLINE_SECONDS.
TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=5.0)

# MySpace issues "srx_" + secrets.token_urlsafe(32): 43 characters of base64url without padding.
_ACCESS_TOKEN_RE = re.compile(r"srx_[A-Za-z0-9_-]{43}")

# Exchange errors that say the caller's token is not good. Any other refusal is our setup.
_CALLER_GRANT_REFUSED = "invalid_grant"
_CALLER_SCOPE_REFUSED = "invalid_scope"

UNAVAILABLE_TEXT = "MySpace временно недоступен, повторите позже"
_FALLBACK_TEXT = "MySpace отклонил запрос"


@dataclass(frozen=True)
class IssuedToken:
    access_token: str
    scopes: frozenset[str]
    expires_in: int


class MyspaceUnavailable(Exception):
    """No answer, a timeout, 5xx or an answer that is not the documented one."""


class GatewayMisconfigured(MyspaceUnavailable):
    """MySpace refused the gateway itself (its client, audience, request): our setup, not the caller."""


class ExchangeRefused(Exception):
    """``invalid_grant``: the caller's token is unknown, revoked, expired or of another audience."""

    def __init__(self, status: int, code: str) -> None:
        super().__init__(f"{status} {code}")
        self.status = status
        self.code = code


class ExchangeScopeRefused(ExchangeRefused):
    """``invalid_scope``: the caller's token, or the employee, lacks a scope the gateway asks for."""


def looks_like_access_token(token: str) -> bool:
    """The shape MySpace issues. Anything else is refused here and never sent to MySpace."""
    return _ACCESS_TOKEN_RE.fullmatch(token) is not None


def log_unavailable(exc: MyspaceUnavailable) -> None:
    """A refusal of the gateway itself needs a person; MySpace being down passes by itself."""
    if isinstance(exc, GatewayMisconfigured):
        logger.error("mcp_gateway: {reason}", reason=str(exc))
    else:
        logger.warning("mcp_gateway: {reason}", reason=str(exc))


class ExtTokenRejected(Exception):
    """401 on a read: the exchanged token died (expired, revoked with its parent, client off)."""


class ExtRequestFailed(Exception):
    """400/403/404/429 on a read. ``message`` is MySpace's Russian text, safe to show the model."""

    def __init__(self, status: int, message: str, retry_after: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.retry_after = retry_after


def _basic_header(client_id: str, client_secret: str) -> str:
    # RFC 6749 §2.3.1: both parts are form-encoded before Basic; MySpace decodes them.
    pair = f"{quote(client_id, safe='')}:{quote(client_secret, safe='')}"
    return "Basic " + base64.b64encode(pair.encode()).decode()


def _json_object(response: httpx.Response) -> dict[str, Any] | None:
    try:
        body = response.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


class MyspaceExtClient:
    def __init__(
        self, http: httpx.AsyncClient, base_url: str, client_id: str, client_secret: str
    ) -> None:
        self._http = http
        self._base_url = base_url
        self._client_id = client_id
        self._client_secret = client_secret

    async def exchange(self, subject_token: str, scopes: frozenset[str]) -> IssuedToken:
        """The caller's sr-mcp token -> a myspace-ext token of the same employee."""
        form = {
            "grant_type": EXCHANGE_GRANT_TYPE,
            "subject_token": subject_token,
            "subject_token_type": ACCESS_SUBJECT_TYPE,
            "audience": DATA_AUDIENCE,
            "scope": " ".join(sorted(scopes)),
        }
        try:
            response = await self._http.post(
                self._base_url + EXCHANGE_PATH,
                data=form,
                headers={
                    "Authorization": _basic_header(self._client_id, self._client_secret),
                    "Accept": "application/json",
                },
            )
        except httpx.HTTPError as exc:
            raise MyspaceUnavailable(f"token exchange: {type(exc).__name__}") from exc
        body = _json_object(response)
        status = response.status_code
        if 400 <= status < 500:
            code = str((body or {}).get("error") or "")
            if code == _CALLER_GRANT_REFUSED:
                raise ExchangeRefused(status, code)
            if code == _CALLER_SCOPE_REFUSED:
                raise ExchangeScopeRefused(status, code)
            # invalid_client, invalid_target, invalid_request, an unknown code or no JSON at all.
            raise GatewayMisconfigured(
                f"token exchange refused the gateway: HTTP {status} {code!r}"
            )
        if status != 200 or body is None:
            raise MyspaceUnavailable(f"token exchange: HTTP {response.status_code}")
        try:
            return IssuedToken(
                access_token=str(body["access_token"]),
                scopes=frozenset(str(body.get("scope") or "").split()),
                expires_in=int(body["expires_in"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise MyspaceUnavailable("token exchange: unexpected answer") from exc

    async def get(
        self, path: str, access_token: str, params: dict[str, str] | None = None
    ) -> dict[str, Any]:
        """GET ``/ext/v1/<path>`` -> its JSON object as it is."""
        try:
            response = await self._http.get(
                self._base_url + path,
                params=params,
                headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise MyspaceUnavailable(f"GET {path}: {type(exc).__name__}") from exc
        status = response.status_code
        body = _json_object(response)
        if status == 200 and body is not None:
            return body
        if status == 401:
            raise ExtTokenRejected
        if status in (400, 403, 404, 429):
            if body is None:
                logger.warning(
                    "mcp_gateway: GET {path} -> HTTP {status} without JSON",
                    path=path,
                    status=status,
                )
            message = str((body or {}).get("message") or _FALLBACK_TEXT)
            raise ExtRequestFailed(status, message, response.headers.get("Retry-After"))
        logger.warning("mcp_gateway: GET {path} -> HTTP {status}", path=path, status=status)
        raise MyspaceUnavailable(f"GET {path}: HTTP {status}")
