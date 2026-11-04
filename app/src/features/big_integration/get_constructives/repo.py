from src.php_compat.pdo_values import NO_MODULE_CODE

from ..sp_reader import BigIntegrationSpReader


class GetConstructivesRepository(BigIntegrationSpReader):
    async def bi_get_client_request_material_json(
        self,
        client_request_id: str | None,
    ) -> object | None:
        return await self.read_scalar(
            "rest.bi_get_client_request_material_json",
            client_request_id,
            module_code=NO_MODULE_CODE,
            text_casts=("integer",),
        )
