import json

from src.repository.base import SpRow, SpRows

from ..sp_reader import BigIntegrationSpReader


class RequestConstructivesRepository(BigIntegrationSpReader):
    async def ddu_client_material_read(self, payload: object) -> SpRows:
        return await self.read_rows("rest.ddu_client_material__read", json.dumps(payload))

    async def ddu_client_filling_read(self, payload: object) -> SpRows:
        return await self.read_rows("rest.ddu_client_filling__read", json.dumps(payload))

    async def ddu_request_full_info(self, payload: object) -> SpRow | None:
        return await self.read_row("rest.ddu_request_full_info", json.dumps(payload))
