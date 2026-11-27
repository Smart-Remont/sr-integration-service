import base64
import json
from datetime import UTC, datetime

import httpx
import pytest

from src.features.installment.bif import client as client_module
from src.features.installment.bif.client import BIFClient, BIFClientError
from src.features.installment.bif.working_days import ALMATY, add_working_days

BASE = "https://gateway.bif.example"


@pytest.fixture
def gateway(monkeypatch):
    """Routes the client's httpx calls to ``handler`` set by the test."""
    state: dict = {"requests": []}
    real_client = httpx.AsyncClient

    def _handler(request: httpx.Request) -> httpx.Response:
        state["requests"].append(request)
        return state["handler"](request)

    def _factory(*args, **kwargs):
        return real_client(*args, transport=httpx.MockTransport(_handler), **kwargs)

    monkeypatch.setattr(client_module.httpx, "AsyncClient", _factory)
    return state


@pytest.mark.asyncio
async def test_token_is_issued_by_client_id_and_secret(gateway):
    gateway["handler"] = lambda request: httpx.Response(
        200, json={"accessToken": "a", "refreshToken": "r", "expiresIn": 300}
    )

    token = await BIFClient().issue_token(BASE, "id", "secret", timeout_seconds=5)

    assert (token.access_token, token.refresh_token, token.expires_in) == ("a", "r", 300)
    request = gateway["requests"][0]
    assert request.url == f"{BASE}/api/auth/token"
    assert json.loads(request.content) == {"clientId": "id", "clientSecret": "secret"}
    assert "Authorization" not in request.headers


@pytest.mark.asyncio
async def test_order_goes_with_bearer_token(gateway):
    gateway["handler"] = lambda request: httpx.Response(
        200, json={"code": "SUCCESS", "appId": "1042", "message": "Заявка создана"}
    )

    answer = await BIFClient().create_order(BASE, "tok", {"partnerId": "SR-1"}, timeout_seconds=5)

    assert answer["appId"] == "1042"
    request = gateway["requests"][0]
    assert request.url == f"{BASE}/api/callbacks/mortgage/orders"
    assert request.headers["Authorization"] == "Bearer tok"


@pytest.mark.asyncio
async def test_error_keeps_bank_message(gateway):
    body = {
        "status": 400,
        "error": "Bad Request",
        "message": "Сумма вне границ (AMOUNT_OUT_OF_RANGE)",
    }
    gateway["handler"] = lambda request: httpx.Response(400, json=body)

    with pytest.raises(BIFClientError) as exc:
        await BIFClient().create_order(BASE, "tok", {}, timeout_seconds=5)

    assert exc.value.status_code == 400
    assert exc.value.message == "Сумма вне границ (AMOUNT_OUT_OF_RANGE)"
    assert exc.value.body == body
    assert exc.value.maybe_sent is False


@pytest.mark.asyncio
async def test_5xx_may_have_reached_the_bank(gateway):
    gateway["handler"] = lambda request: httpx.Response(503, text="down")

    with pytest.raises(BIFClientError) as exc:
        await BIFClient().create_order(BASE, "tok", {}, timeout_seconds=5)

    assert exc.value.maybe_sent is True


@pytest.mark.asyncio
async def test_read_timeout_may_have_reached_the_bank(gateway):
    def _timeout(request):
        raise httpx.ReadTimeout("slow", request=request)

    gateway["handler"] = _timeout

    with pytest.raises(BIFClientError) as exc:
        await BIFClient().create_order(BASE, "tok", {}, timeout_seconds=5)

    assert exc.value.maybe_sent is True


@pytest.mark.asyncio
async def test_connect_error_did_not_reach_the_bank(gateway):
    def _refused(request):
        raise httpx.ConnectError("refused", request=request)

    gateway["handler"] = _refused

    with pytest.raises(BIFClientError) as exc:
        await BIFClient().create_order(BASE, "tok", {}, timeout_seconds=5)

    assert exc.value.maybe_sent is False


def test_jwt_expiry_is_read_without_verification():
    claims = base64.urlsafe_b64encode(json.dumps({"exp": 4102444800}).encode()).decode().rstrip("=")
    assert BIFClient.access_token_expires_at(f"h.{claims}.s") == datetime(2100, 1, 1, tzinfo=UTC)
    assert BIFClient.access_token_expires_at("opaque") is None


# --- working days --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("start", "expected"),
    [
        # Monday 10:00 → Thursday 10:00
        (
            datetime(2026, 11, 23, 10, 0, tzinfo=ALMATY),
            datetime(2026, 11, 26, 10, 0, tzinfo=ALMATY),
        ),
        # Friday 10:00 → Wednesday 10:00
        (datetime(2026, 11, 27, 10, 0, tzinfo=ALMATY), datetime(2026, 12, 2, 10, 0, tzinfo=ALMATY)),
        # Saturday → counted from Monday 00:00 → Thursday 00:00
        (datetime(2026, 11, 28, 15, 0, tzinfo=ALMATY), datetime(2026, 12, 3, 0, 0, tzinfo=ALMATY)),
        # Sunday late evening, as UTC → Thursday 00:00 Almaty
        (datetime(2026, 11, 29, 17, 0, tzinfo=UTC), datetime(2026, 12, 3, 0, 0, tzinfo=ALMATY)),
    ],
)
def test_add_three_working_days(start, expected):
    assert add_working_days(start, 3) == expected


def test_zero_days_on_weekday_is_now():
    moment = datetime(2026, 11, 24, 9, 0, tzinfo=ALMATY)
    assert add_working_days(moment, 0) == moment
