from src.php_compat.pdo_values import NO_MODULE_CODE

from ..sp_reader import BigIntegrationSpReader


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
