"""BI Finance gateway: token, token refresh and «беззалоговый кредит» order (ТЗ, разделы 2, 3, 5).

No database here. Request bodies are not logged: they carry the borrower's IIN and phone and the
client secret.
"""

import base64
import json
from datetime import UTC, datetime
from typing import Any, ClassVar

import httpx
from loguru import logger


class BIFClientError(Exception):
    """A failed call. ``message`` — BI Finance's ``message`` (shown to the manager), ``body`` — the
    whole answer for the event log. ``maybe_sent`` — the order may have reached the bank (read
    timeout, dropped connection, 5xx): a repeat must go with the same partnerId."""

    def __init__(
        self,
        status_code: int,
        message: str,
        *,
        body: Any = None,
        maybe_sent: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.body = body
        self.maybe_sent = maybe_sent


class BIFToken:
    __slots__ = ("access_token", "expires_in", "refresh_token")

    def __init__(
        self, access_token: str, refresh_token: str | None, expires_in: int | None
    ) -> None:
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.expires_in = expires_in


class BIFClient:
    _HEADERS: ClassVar[dict[str, str]] = {
        "Accept": "application/json",
        "User-Agent": "SmartRemont-integrations-sr/1.0",
    }

    async def issue_token(
        self, base_url: str, client_id: str, client_secret: str, *, timeout_seconds: float
    ) -> BIFToken:
        answer = await self._post(
            base_url,
            "/api/auth/token",
            {"clientId": client_id, "clientSecret": client_secret},
            timeout_seconds=timeout_seconds,
        )
        return self._token(answer)

    async def refresh_token(
        self, base_url: str, refresh_token: str, *, timeout_seconds: float
    ) -> BIFToken:
        answer = await self._post(
            base_url,
            "/api/auth/token/refresh",
            {"refreshToken": refresh_token},
            timeout_seconds=timeout_seconds,
        )
        return self._token(answer)

    async def create_order(
        self, base_url: str, access_token: str, payload: dict[str, Any], *, timeout_seconds: float
    ) -> dict[str, Any]:
        return await self._post(
            base_url,
            "/api/callbacks/mortgage/orders",
            payload,
            timeout_seconds=timeout_seconds,
            access_token=access_token,
        )

    @staticmethod
    def access_token_expires_at(access_token: str) -> datetime | None:
        """``exp`` of a JWT access token, if it is one. Not verified: only a cache hint."""
        parts = access_token.split(".")
        if len(parts) != 3:
            return None
        try:
            padded = parts[1] + "=" * (-len(parts[1]) % 4)
            claims = json.loads(base64.urlsafe_b64decode(padded))
        except ValueError:
            return None
        exp = claims.get("exp") if isinstance(claims, dict) else None
        if not isinstance(exp, int | float):
            return None
        return datetime.fromtimestamp(exp, tz=UTC)

    @staticmethod
    def _token(answer: dict[str, Any]) -> BIFToken:
        # The ТЗ names no fields of the token answer: take both spellings.
        access = answer.get("accessToken") or answer.get("access_token") or answer.get("access")
        if not isinstance(access, str) or not access:
            raise BIFClientError(502, "BI Finance не вернул access-токен.", body=_masked(answer))
        refresh = answer.get("refreshToken") or answer.get("refresh_token") or answer.get("refresh")
        expires_in = answer.get("expiresIn") or answer.get("expires_in")
        return BIFToken(
            access_token=access,
            refresh_token=refresh if isinstance(refresh, str) and refresh else None,
            expires_in=int(expires_in) if isinstance(expires_in, int | float) else None,
        )

    async def _post(
        self,
        base_url: str,
        path: str,
        payload: dict[str, Any],
        *,
        timeout_seconds: float,
        access_token: str | None = None,
    ) -> dict[str, Any]:
        headers = dict(self._HEADERS)
        if access_token is not None:
            headers["Authorization"] = f"Bearer {access_token}"
        url = f"{base_url.rstrip('/')}{path}"
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(timeout_seconds, connect=10.0), headers=headers
            ) as client:
                response = await client.post(url, json=payload)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            # The request never left: safe to report as not sent.
            raise BIFClientError(503, f"BI Finance недоступен: {exc}") from exc
        except httpx.HTTPError as exc:
            raise BIFClientError(
                504, f"BI Finance не ответил: {type(exc).__name__}", maybe_sent=True
            ) from exc

        body = _json_or_text(response)
        logger.info(
            "BIF HTTP | path={path} status={status}", path=path, status=response.status_code
        )
        if response.is_success:
            if not isinstance(body, dict):
                raise BIFClientError(502, "BI Finance вернул ответ не в JSON.", body=body)
            return body

        message = body.get("message") if isinstance(body, dict) else None
        if not isinstance(message, str) or not message.strip():
            message = body if isinstance(body, str) and body.strip() else response.reason_phrase
        raise BIFClientError(
            response.status_code,
            str(message).strip(),
            body=body,
            maybe_sent=response.status_code >= 500,
        )


def _json_or_text(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text[:2000]


def _masked(answer: dict[str, Any]) -> dict[str, Any]:
    return {key: "***" if "token" in key.lower() else value for key, value in answer.items()}
