from ..sp_reader import NO_MODULE_CODE, BigIntegrationSpReader


class RequestFormsRepository(BigIntegrationSpReader):
    async def request_from_bigapp(self, payload: str) -> object | None:
        return await self.read_scalar(
            "crm.request_from_bigapp",
            payload,
            module_code=NO_MODULE_CODE,
        )

    async def request_from_bigcrm(self, payload: str) -> object | None:
        return await self.read_scalar(
            "crm.request_from_bigcrm",
            payload,
            module_code=NO_MODULE_CODE,
        )
