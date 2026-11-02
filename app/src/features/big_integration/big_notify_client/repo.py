from src.repository import BaseRepository, scalar_from_sp_rows

from ..sp_reader import DDU_MODULE_CODE, php_database_error


class BigNotifyClientRepository(BaseRepository):
    async def big_notify_client(self, body: str) -> object | None:
        try:
            rows = await self.call_sp("rest.big_notify_client", body, module_code=DDU_MODULE_CODE)
        except Exception as exc:
            raise php_database_error(exc) from exc
        return scalar_from_sp_rows(rows)
