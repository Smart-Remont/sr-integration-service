from collections.abc import AsyncGenerator
from functools import partial
from typing import Annotated

import httpx
from asyncpg import Connection, Pool
from fastapi import Depends

from src.config import auto_sign_config
from src.database.pool import get_db_pool
from src.php_compat.remote_file import file_get_contents
from src.shared.cron_run import held_run_connection
from src.shared.mynca_client import MyncaClient

from .agreement_sum import AgreementSumService
from .document_upload import DocumentUploadService
from .failure_brake import SigningFailureBrake, signing_failure_brake
from .operator_signing import OperatorSigningService
from .repo import AutoSignRepository


async def get_auto_sign_connection(
    db_pool: Annotated[Pool, Depends(get_db_pool)],
) -> AsyncGenerator[Connection]:
    """No transaction stays open: each step commits on its own while MyNCA or a partner answers."""
    async with held_run_connection(db_pool, cron_name="Auto sign") as connection:
        yield connection


def get_auto_sign_repository(
    # function scope: the connection, and with it the lock, goes back before the response is sent.
    connection: Annotated[Connection, Depends(get_auto_sign_connection, scope="function")],
) -> AutoSignRepository:
    return AutoSignRepository(connection=connection)


def get_auto_sign_mynca() -> MyncaClient:
    return MyncaClient(auto_sign_config.mynca_base_url, auto_sign_config.mynca_token)


def get_signing_failure_brake() -> SigningFailureBrake:
    """One per worker: services live for one request, and the brake must outlive them."""
    return signing_failure_brake


def get_partner_transport() -> httpx.AsyncBaseTransport | None:
    """Overridden by tests. ``None`` is the real network."""
    return None


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


def get_agreement_sum_service(
    repository: RepositoryDep,
    transport: Annotated[httpx.AsyncBaseTransport | None, Depends(get_partner_transport)],
) -> AgreementSumService:
    return AgreementSumService(
        repository,
        partial(file_get_contents, transport=transport),
        auto_sign_config.partner_url,
    )


OperatorSigningServiceDep = Annotated[
    OperatorSigningService,
    Depends(get_operator_signing_service),
]
DocumentUploadServiceDep = Annotated[DocumentUploadService, Depends(get_document_upload_service)]
AgreementSumServiceDep = Annotated[AgreementSumService, Depends(get_agreement_sum_service)]
