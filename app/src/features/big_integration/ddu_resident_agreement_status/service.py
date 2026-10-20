from typing import TYPE_CHECKING

from fastapi.responses import JSONResponse

from ..errors import BigIntegrationDatabaseError
from ..responses import big_integration_error_response, big_integration_success_response

if TYPE_CHECKING:
    from .repo import DduResidentAgreementStatusRepository


class DduResidentAgreementStatusService:
    def __init__(self, repository: DduResidentAgreementStatusRepository) -> None:
        self.repository = repository

    async def ddu_resident_agreement_status(
        self,
        body: dict[str, object],
    ) -> JSONResponse:
        try:
            await self.repository.ddu_resident_agreement_log_insert(body)
            return big_integration_success_response(None)
        except BigIntegrationDatabaseError as exc:
            return big_integration_error_response(exc.message)
