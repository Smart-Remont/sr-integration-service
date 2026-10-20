import time

import pytest
from fastapi import HTTPException

from src.integration_context.token import (
    IntegrationContextError,
    sign_integration_context,
    verify_integration_context,
)

SECRET = "test-integration-context-secret"


def test_sign_and_verify_roundtrip():
    token = sign_integration_context(
        secret=SECRET,
        employee_id=42,
        scopes=["factoring"],
        ctx={"factoring": {"cr": 2916069, "app": 123}},
    )
    ctx = verify_integration_context(token, secret=SECRET)
    assert ctx.employee_id == 42
    assert ctx.scopes == ("factoring",)
    assert ctx.ctx["factoring"]["cr"] == 2916069
    assert ctx.ctx["factoring"]["app"] == 123


def test_require_client_request_match():
    token = sign_integration_context(
        secret=SECRET,
        employee_id=1,
        scopes=["installment"],
        ctx={"installment": {"cr": 100}},
    )
    ctx = verify_integration_context(token, secret=SECRET)
    ctx.require_client_request("installment", 100)


def test_require_client_request_mismatch():
    token = sign_integration_context(
        secret=SECRET,
        employee_id=1,
        scopes=["factoring"],
        ctx={"factoring": {"cr": 100}},
    )
    ctx = verify_integration_context(token, secret=SECRET)
    with pytest.raises(HTTPException) as exc:
        ctx.require_client_request("factoring", 999)
    assert exc.value.status_code == 403


def test_expired_token():
    token = sign_integration_context(
        secret=SECRET,
        employee_id=1,
        scopes=["factoring"],
        ttl_seconds=-1,
    )
    time.sleep(0.01)
    with pytest.raises(IntegrationContextError, match="expired"):
        verify_integration_context(token, secret=SECRET)


def test_invalid_signature():
    token = sign_integration_context(
        secret=SECRET,
        employee_id=1,
        scopes=["factoring"],
    )
    tampered = token[:-4] + "xxxx"
    with pytest.raises(IntegrationContextError, match="signature"):
        verify_integration_context(tampered, secret=SECRET)
