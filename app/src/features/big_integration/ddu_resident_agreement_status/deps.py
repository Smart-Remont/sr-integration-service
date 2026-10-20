from typing import Annotated

from fastapi import Depends

from src.database.deps import DatabaseConnectionDep

from .repo import DduResidentAgreementStatusRepository
from .service import DduResidentAgreementStatusService


def get_ddu_resident_agreement_status_service(
    connection: DatabaseConnectionDep,
) -> DduResidentAgreementStatusService:
    repository = DduResidentAgreementStatusRepository(connection=connection)
    return DduResidentAgreementStatusService(repository=repository)


DduResidentAgreementStatusServiceDep = Annotated[
    DduResidentAgreementStatusService,
    Depends(get_ddu_resident_agreement_status_service),
]
