from typing import TYPE_CHECKING

from fastapi.responses import JSONResponse

from ..errors import BigIntegrationDatabaseError
from ..responses import big_integration_error_response, big_integration_success_response

if TYPE_CHECKING:
    from .repo import RequestCreateV3Repository


class RequestCreateV3Service:
    def __init__(self, repository: RequestCreateV3Repository) -> None:
        self.repository = repository

    async def request_create_v3(self, body: dict[str, object]) -> JSONResponse:
        try:
            client_request_id = await self.repository.ddu_create_request_v2(body)
            row = await self.repository.ddu_request_get(client_request_id)
            return big_integration_success_response(row)
        except BigIntegrationDatabaseError as exc:
            return big_integration_error_response(exc.message)
