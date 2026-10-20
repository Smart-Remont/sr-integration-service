import json
from datetime import date, datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from fastapi import status
from fastapi.responses import JSONResponse

from ..responses import big_integration_error_response, big_integration_success_response

if TYPE_CHECKING:
    from src.repository.base import SpRow

    from .repo import NpsResultsRepository

ALMATY_TZ = ZoneInfo("Asia/Almaty")

# Whole history since 2023 is ~4 000 surveys; a year per call keeps responses small.
MAX_PERIOD_DAYS = 366


class NpsResultsService:
    def __init__(self, repository: NpsResultsRepository) -> None:
        self.repository = repository

    async def nps_results(self, date_from: str | None, date_to: str | None) -> JSONResponse:
        """Surveys for the period; no dates means yesterday (decided in the DB function).

        Read-only: nothing is marked as sent, so BI can repeat a request for the same days.
        """
        if (date_from is None) != (date_to is None):
            return _bad_request("Укажите оба параметра date_from и date_to или ни одного")

        period_from = _parse_date(date_from)
        period_to = _parse_date(date_to)
        if (date_from is not None and period_from is None) or (
            date_to is not None and period_to is None
        ):
            return _bad_request("Даты date_from и date_to должны быть в формате YYYY-MM-DD")

        if period_from is not None and period_to is not None:
            if period_from > period_to:
                return _bad_request("date_from не может быть позже date_to")
            if (period_to - period_from).days >= MAX_PERIOD_DAYS:
                return _bad_request(f"Период не может быть больше {MAX_PERIOD_DAYS} дней")

        rows = await self.repository.bi_nps_results(period_from, period_to)
        return big_integration_success_response([_to_response_row(row) for row in rows])


def _parse_date(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _to_response_row(row: SpRow) -> SpRow:
    result = dict(row)
    # asyncpg returns jsonb as text (the pool has no JSON codec) and timestamptz in UTC;
    # BI gets a JSON array and local Almaty time with its offset.
    answers = result.get("answers")
    if isinstance(answers, str):
        result["answers"] = json.loads(answers)
    evaluated_at = result.get("evaluation_datetime")
    if isinstance(evaluated_at, datetime) and evaluated_at.tzinfo is not None:
        result["evaluation_datetime"] = evaluated_at.astimezone(ALMATY_TZ)
    return result


def _bad_request(message: str) -> JSONResponse:
    return big_integration_error_response(message, status_code=status.HTTP_400_BAD_REQUEST)
