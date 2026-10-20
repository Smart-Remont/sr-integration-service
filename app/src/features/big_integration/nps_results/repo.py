from datetime import date

from src.repository import BaseRepository
from src.repository.base import SpRows

_DDU_MODULE_CODE = "DDU"


class NpsResultsRepository(BaseRepository):
    async def bi_nps_results(self, date_from: date | None, date_to: date | None) -> SpRows:
        rows = await self.call_sp(
            "rest.bi_nps_results",
            date_from,
            date_to,
            cursor=True,
            module_code=_DDU_MODULE_CODE,
        )
        if not isinstance(rows, list):
            raise TypeError("rest.bi_nps_results must return a single refcursor")
        return rows
