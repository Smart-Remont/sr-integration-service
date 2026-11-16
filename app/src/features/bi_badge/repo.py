from src.repository import BaseRepository, scalar_from_sp_rows
from src.repository.base import SpRows


class BiBadgeRepository(BaseRepository):
    async def resident_read(self) -> SpRows:
        return await self.call_sp("crm.bi_badge_resident_read", None, cursor=True)

    async def request_from_bi_badge(self, payload: str) -> object | None:
        rows = await self.call_sp("crm.request_from_bi_badge", payload)
        return scalar_from_sp_rows(rows)
