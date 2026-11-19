from ..sp_reader import DDU_MODULE_CODE, BigIntegrationSpReader


class BigNotifyClientRepository(BigIntegrationSpReader):
    async def big_notify_client(self, body: str) -> object | None:
        return await self.read_scalar("rest.big_notify_client", body, module_code=DDU_MODULE_CODE)
