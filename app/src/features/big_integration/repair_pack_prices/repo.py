from src.repository import BaseRepository
from src.repository.base import SpRow

from ..errors import to_big_integration_database_error

_DDU_MODULE_CODE = "DDU"


class RepairPackPricesRepository(BaseRepository):
    async def ddu_repair_pack_info_get(self, placement_uuid: str) -> SpRow | None:
        # The service reports a failed placement and goes on to the next one in the same
        # request transaction. Without a savepoint the first RAISE aborts that transaction
        # and every later placement fails with "current transaction is aborted".
        try:
            async with self.connection.transaction():
                rows = await self.call_sp(
                    "rest.ddu_repair_pack_info__get",
                    placement_uuid,
                    cursor=True,
                    module_code=_DDU_MODULE_CODE,
                )
        except Exception as exc:
            raise to_big_integration_database_error(exc) from exc

        if not rows:
            return None

        return rows[0]
