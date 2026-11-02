from src.repository import scalar_from_sp_rows

from ..sp_reader import DDU_MODULE_CODE, BigIntegrationSpReader, php_database_error


class BigNotifyClientRepository(BigIntegrationSpReader):
    async def big_notify_client(self, body: str) -> object | None:
        try:
            await self._plan_like_a_new_session()
            rows = await self.call_sp("rest.big_notify_client", body, module_code=DDU_MODULE_CODE)
        except Exception as exc:
            raise php_database_error(exc) from exc
        return scalar_from_sp_rows(rows)
