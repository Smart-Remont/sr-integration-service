from typing import TYPE_CHECKING

from fastapi import status
from fastapi.responses import JSONResponse

from ..errors import BigIntegrationDatabaseError
from ..responses import big_integration_error_response, big_integration_success_response

if TYPE_CHECKING:
    from .repo import DduFlatInfoMultipleRepository


class DduFlatInfoMultipleService:
    def __init__(self, repository: DduFlatInfoMultipleRepository) -> None:
        self.repository = repository

    async def ddu_flat_info_multiple(self, body: dict[str, object]) -> JSONResponse:
        flat_guids = body.get("flat_guids")
        if not isinstance(flat_guids, list) or not flat_guids:
            return big_integration_error_response(
                'Поле "flat_guids" не заполнено',
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        normalized_flat_guids = [str(flat_guid) for flat_guid in flat_guids]

        try:
            rows = await self.repository.ddu_flat_info_multiple(normalized_flat_guids)
            return big_integration_success_response(rows)
        except BigIntegrationDatabaseError as exc:
            return big_integration_error_response(exc.message)
