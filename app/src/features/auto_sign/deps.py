from typing import Annotated

from fastapi import Depends

from src.config import auto_sign_config
from src.shared.cron_run import run_connection_dep
from src.shared.mynca_client import MyncaClient

from .document_upload import DocumentUploadService
from .failure_brake import SigningFailureBrake, signing_failure_brake
from .operator_signing import OperatorSigningService
from .repo import AutoSignRepository

_Connection = run_connection_dep("Auto sign")


def get_auto_sign_repository(connection: _Connection) -> AutoSignRepository:
    return AutoSignRepository(connection=connection)


def get_auto_sign_mynca() -> MyncaClient:
    return MyncaClient(auto_sign_config.mynca_base_url, auto_sign_config.mynca_token)


def get_signing_failure_brake() -> SigningFailureBrake:
    """One per worker: services live for one request, and the brake must outlive them."""
    return signing_failure_brake


RepositoryDep = Annotated[AutoSignRepository, Depends(get_auto_sign_repository)]
MyncaDep = Annotated[MyncaClient, Depends(get_auto_sign_mynca)]


def get_operator_signing_service(
    repository: RepositoryDep,
    mynca: MyncaDep,
    brake: Annotated[SigningFailureBrake, Depends(get_signing_failure_brake)],
) -> OperatorSigningService:
    return OperatorSigningService(repository, mynca, auto_sign_config.master_key, brake)


def get_document_upload_service(
    repository: RepositoryDep,
    mynca: MyncaDep,
) -> DocumentUploadService:
    return DocumentUploadService(repository, mynca)


OperatorSigningServiceDep = Annotated[
    OperatorSigningService,
    Depends(get_operator_signing_service),
]
DocumentUploadServiceDep = Annotated[DocumentUploadService, Depends(get_document_upload_service)]
