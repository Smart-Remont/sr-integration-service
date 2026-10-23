"""GET /api/big_integration/nps-results: NPS surveys for BI Group by period.

The repository is replaced by a mock, so these tests cover auth, date checks and how
rows from ``rest.bi_nps_results`` are turned into the BIG envelope.
"""

import base64
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from src.config import big_integration_auth_config
from src.features.big_integration.nps_results.deps import get_nps_results_service
from src.features.big_integration.nps_results.service import NpsResultsService
from src.main import app

URL = "/api/big_integration/nps-results"

SURVEY_ROW = {
    "nps_survey_id": 10467,
    "survey_type": "REPAIR",
    "client_request_id": 2916069,
    "evaluation_datetime": datetime(2026, 7, 31, 6, 18, 15, tzinfo=UTC),
    "recommendation_rating": 9,
    "answers": '[{"question_code": "SUPPORT_NPS", "rating": 9, "aspects": []}]',
}


@pytest.fixture
def repository() -> AsyncMock:
    repository = AsyncMock()
    repository.bi_nps_results.return_value = []
    return repository


@pytest.fixture
async def client(repository: AsyncMock) -> AsyncIterator[AsyncClient]:
    app.dependency_overrides[get_nps_results_service] = lambda: NpsResultsService(repository)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
    app.dependency_overrides.clear()


def _auth(password: str | None = None) -> dict[str, str]:
    user = big_integration_auth_config.username
    secret = password if password is not None else big_integration_auth_config.password
    token = base64.b64encode(f"{user}:{secret}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _error(message: str) -> dict[str, Any]:
    return {"data": None, "response": False, "error": {"message": message}}


async def test_requires_basic_auth(client: AsyncClient, repository: AsyncMock) -> None:
    assert (await client.get(URL)).status_code == 401
    assert (await client.get(URL, headers=_auth("wrong"))).status_code == 401
    repository.bi_nps_results.assert_not_called()


async def test_period_is_passed_as_dates(client: AsyncClient, repository: AsyncMock) -> None:
    params = {"date_from": "2026-10-01", "date_to": "2026-10-19"}

    response = await client.get(URL, params=params, headers=_auth())

    assert response.status_code == 200
    assert response.json() == {"data": [], "response": True, "error": {"message": ""}}
    repository.bi_nps_results.assert_awaited_once_with(date(2026, 10, 1), date(2026, 10, 19))


async def test_without_dates_the_database_picks_yesterday(
    client: AsyncClient, repository: AsyncMock
) -> None:
    response = await client.get(URL, headers=_auth())

    assert response.status_code == 200
    repository.bi_nps_results.assert_awaited_once_with(None, None)


async def test_row_has_answers_array_and_almaty_time(
    client: AsyncClient, repository: AsyncMock
) -> None:
    repository.bi_nps_results.return_value = [SURVEY_ROW]

    response = await client.get(URL, headers=_auth())

    row = response.json()["data"][0]
    assert row["answers"] == [{"question_code": "SUPPORT_NPS", "rating": 9, "aspects": []}]
    assert row["evaluation_datetime"] == "2026-07-31T11:18:15+05:00"
    assert row["recommendation_rating"] == 9


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"date_from": "2026-10-01"}, "Укажите оба параметра date_from и date_to или ни одного"),
        ({"date_to": "2026-10-01"}, "Укажите оба параметра date_from и date_to или ни одного"),
        (
            {"date_from": "01.10.2026", "date_to": "2026-10-19"},
            "Даты date_from и date_to должны быть в формате YYYY-MM-DD",
        ),
        (
            {"date_from": "2026-10-19", "date_to": "2026-10-01"},
            "date_from не может быть позже date_to",
        ),
        (
            {"date_from": "2025-01-01", "date_to": "2026-01-02"},
            "Период не может быть больше 366 дней",
        ),
    ],
)
async def test_bad_period_is_rejected_before_the_database(
    client: AsyncClient, repository: AsyncMock, params: dict[str, str], message: str
) -> None:
    response = await client.get(URL, params=params, headers=_auth())

    assert response.status_code == 400
    assert response.json() == _error(message)
    repository.bi_nps_results.assert_not_called()


async def test_full_year_is_allowed(client: AsyncClient, repository: AsyncMock) -> None:
    params = {"date_from": "2024-01-01", "date_to": "2024-12-31"}

    response = await client.get(URL, params=params, headers=_auth())

    assert response.status_code == 200
