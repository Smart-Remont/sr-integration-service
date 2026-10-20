from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from src.features.factoring.ff.service import FactoringService


def _service(deal_iin: str | None) -> FactoringService:
    repository = MagicMock()
    repository.get_deal_prop_iin = AsyncMock(return_value=deal_iin)
    return FactoringService(repository=repository, client=MagicMock(), app_env="test")


@pytest.mark.asyncio
async def test_deal_iin_match_passes_when_equal():
    svc = _service("891026301046")

    await svc._require_deal_iin_match(2916069, "891026301046")


@pytest.mark.asyncio
async def test_deal_iin_match_ignores_formatting_differences():
    svc = _service("891026-301046")

    await svc._require_deal_iin_match(2916069, "891026301046")


@pytest.mark.asyncio
async def test_deal_iin_match_rejects_different_iin():
    svc = _service("891026301046")

    with pytest.raises(HTTPException) as exc:
        await svc._require_deal_iin_match(2916069, "040516551071")

    assert exc.value.status_code == 422
    assert "891026301046" in exc.value.detail


@pytest.mark.asyncio
async def test_deal_iin_match_rejects_when_deal_has_no_iin():
    svc = _service(None)

    with pytest.raises(HTTPException) as exc:
        await svc._require_deal_iin_match(2916069, "891026301046")

    assert exc.value.status_code == 422
