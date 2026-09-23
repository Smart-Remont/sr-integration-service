import httpx
import pytest

from src.features.factoring.ff.client import FactoringClient, FactoringClientError


def test_error_response_keeps_original_body():
    response = httpx.Response(
        400,
        json={"message": "error", "code": "BAD_PHONE"},
    )

    with pytest.raises(FactoringClientError) as exc:
        FactoringClient._parse_response(response)

    assert exc.value.status_code == 400
    assert "BAD_PHONE" in exc.value.detail
    assert "HTTP 400:" in exc.value.detail


def test_error_response_without_json_keeps_text():
    response = httpx.Response(502, text="upstream timeout")

    with pytest.raises(FactoringClientError) as exc:
        FactoringClient._parse_response(response)

    assert exc.value.detail == "HTTP 502: upstream timeout"
