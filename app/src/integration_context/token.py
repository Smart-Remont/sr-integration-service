from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, status

from .constants import DEFAULT_AUDIENCE, DEFAULT_ISSUER, DEFAULT_TTL_SECONDS


class IntegrationContextError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class IntegrationContext:
    """Verified proxy context from CRM (or another trusted issuer)."""

    employee_id: int
    scopes: tuple[str, ...]
    ctx: dict[str, dict[str, Any]]
    issuer: str
    audience: str

    def require_scope(self, scope: str) -> None:
        if scope not in self.scopes:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Integration context missing scope '{scope}'.",
            )

    def namespace(self, scope: str) -> dict[str, Any]:
        raw = self.ctx.get(scope)
        if not isinstance(raw, dict):
            return {}
        return raw

    def require_client_request(self, scope: str, client_request_id: int) -> None:
        self.require_scope(scope)
        namespace = self.namespace(scope)
        token_cr = namespace.get("cr")
        if token_cr is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Integration context missing ctx.{scope}.cr.",
            )
        if int(token_cr) != int(client_request_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Integration context client_request_id mismatch.",
            )

    def require_application(
        self,
        scope: str,
        application_id: int,
        *,
        client_request_id: int | None = None,
    ) -> None:
        self.require_scope(scope)
        namespace = self.namespace(scope)
        token_app = namespace.get("app")
        if token_app is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Integration context missing ctx.{scope}.app.",
            )
        if int(token_app) != int(application_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Integration context application_id mismatch.",
            )
        if client_request_id is not None:
            token_cr = namespace.get("cr")
            if token_cr is not None and int(token_cr) != int(client_request_id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Integration context client_request_id mismatch.",
                )


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def sign_integration_context(
    *,
    secret: str,
    employee_id: int,
    scopes: list[str],
    ctx: dict[str, dict[str, Any]] | None = None,
    issuer: str = DEFAULT_ISSUER,
    audience: str = DEFAULT_AUDIENCE,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> str:
    if not secret.strip():
        raise IntegrationContextError("INTEGRATION_CONTEXT_SECRET is not configured.")
    if not scopes:
        raise IntegrationContextError("At least one scope is required.")

    now = int(time.time())
    payload: dict[str, Any] = {
        "sub": str(employee_id),
        "iss": issuer,
        "aud": audience,
        "scope": list(scopes),
        "ctx": ctx or {},
        "iat": now,
        "exp": now + ttl_seconds,
    }
    header = _b64url_encode(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{header}.{body}".encode()
    signature = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    return f"{header}.{body}.{_b64url_encode(signature)}"


def verify_integration_context(
    token: str,
    *,
    secret: str,
    audience: str = DEFAULT_AUDIENCE,
    issuers: tuple[str, ...] = (DEFAULT_ISSUER,),
) -> IntegrationContext:
    if not secret.strip():
        raise IntegrationContextError("INTEGRATION_CONTEXT_SECRET is not configured.")

    parts = token.split(".")
    if len(parts) != 3:
        raise IntegrationContextError("Invalid integration context token format.")

    header_raw, body_raw, signature_raw = parts
    signing_input = f"{header_raw}.{body_raw}".encode()
    expected = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    if not hmac.compare_digest(_b64url_encode(expected), signature_raw):
        raise IntegrationContextError("Invalid integration context token signature.")

    try:
        payload = json.loads(_b64url_decode(body_raw))
    except (json.JSONDecodeError, ValueError) as exc:
        raise IntegrationContextError("Invalid integration context token payload.") from exc

    if payload.get("aud") != audience:
        raise IntegrationContextError("Invalid integration context audience.")
    if payload.get("iss") not in issuers:
        raise IntegrationContextError("Invalid integration context issuer.")

    now = int(time.time())
    exp = payload.get("exp")
    if not isinstance(exp, int) or exp < now:
        raise IntegrationContextError("Integration context token expired.")

    sub = payload.get("sub")
    if not isinstance(sub, str) or not sub.isdigit():
        raise IntegrationContextError("Integration context token missing sub (employee_id).")

    raw_scopes = payload.get("scope")
    if not isinstance(raw_scopes, list) or not raw_scopes:
        raise IntegrationContextError("Integration context token missing scope.")
    scopes = tuple(str(item) for item in raw_scopes)

    raw_ctx = payload.get("ctx")
    if raw_ctx is None:
        ctx: dict[str, dict[str, Any]] = {}
    elif not isinstance(raw_ctx, dict):
        raise IntegrationContextError("Integration context ctx must be an object.")
    else:
        ctx = {}
        for scope_key, namespace in raw_ctx.items():
            if not isinstance(scope_key, str):
                raise IntegrationContextError("Integration context ctx keys must be strings.")
            if namespace is None:
                ctx[scope_key] = {}
            elif not isinstance(namespace, dict):
                raise IntegrationContextError(
                    f"Integration context ctx.{scope_key} must be an object."
                )
            else:
                ctx[scope_key] = dict(namespace)

    return IntegrationContext(
        employee_id=int(sub),
        scopes=scopes,
        ctx=ctx,
        issuer=str(payload.get("iss") or ""),
        audience=str(payload.get("aud") or ""),
    )
