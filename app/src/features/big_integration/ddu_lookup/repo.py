from src.repository.base import SpRow

from ..sp_reader import BigIntegrationSpReader


class DduLookupRepository(BigIntegrationSpReader):
    async def ddu_request_info(
        self,
        deal_id: str | None,
        application_id: str | None,
        order_id: str | None,
    ) -> SpRow | None:
        return await self.read_row("rest.ddu_request_info", deal_id, application_id, order_id)

    async def ddu_request_and_status_info(
        self,
        deal_id: str | None,
        application_id: str | None,
        order_id: str | None,
    ) -> SpRow | None:
        return await self.read_row(
            "rest.ddu_request_and_status_info",
            deal_id,
            application_id,
            order_id,
        )

    async def ddu_request_info_by_client_request(
        self, client_request_id: int | None
    ) -> SpRow | None:
        return await self.read_row("rest.ddu_request_info_by_client_request", client_request_id)

    async def ddu_flat_info(self, flat_guid: str | None) -> SpRow | None:
        return await self.read_row("rest.ddu_flat_info", flat_guid)
