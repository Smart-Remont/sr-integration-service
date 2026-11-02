from src.php_compat.json_codec import php_json_encode_default
from src.repository.base import SpRow, SpRows

from ..sp_reader import BigIntegrationSpReader


class RequestConstructivesRepository(BigIntegrationSpReader):
    async def ddu_client_material_read(self, payload: object) -> SpRows:
        return await self.read_rows(
            "rest.ddu_client_material__read", php_json_encode_default(payload)
        )

    async def ddu_client_filling_read(self, payload: object) -> SpRows:
        return await self.read_rows(
            "rest.ddu_client_filling__read", php_json_encode_default(payload)
        )

    async def ddu_request_full_info(self, payload: object) -> SpRow | None:
        return await self.read_row("rest.ddu_request_full_info", php_json_encode_default(payload))
