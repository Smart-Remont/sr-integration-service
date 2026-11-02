from typing import Annotated

from asyncpg import Pool
from fastapi import Depends

from src.config import ClientSignConfig, client_sign_config
from src.database.pool import get_db_pool

from .callback import SignCallbackService
from .client import DocumentFetcher, MyncaClient
from .document_list import DocumentListService
from .documents import DocumentSource
from .downloads import DownloadService
from .repo import ClientSignDatabase
from .signing import SigningService


def get_client_sign_config() -> ClientSignConfig:
    return client_sign_config


def get_client_sign_database(
    pool: Annotated[Pool, Depends(get_db_pool)],
) -> ClientSignDatabase:
    return ClientSignDatabase(pool)


def get_mynca_client(
    config: Annotated[ClientSignConfig, Depends(get_client_sign_config)],
) -> MyncaClient:
    return MyncaClient(config.mynca_base_url, config.mynca_token)


def get_document_fetcher() -> DocumentFetcher:
    return DocumentFetcher()


ConfigDep = Annotated[ClientSignConfig, Depends(get_client_sign_config)]
DatabaseDep = Annotated[ClientSignDatabase, Depends(get_client_sign_database)]
MyncaDep = Annotated[MyncaClient, Depends(get_mynca_client)]
FetcherDep = Annotated[DocumentFetcher, Depends(get_document_fetcher)]


def get_document_list_service(database: DatabaseDep) -> DocumentListService:
    return DocumentListService(database)


def get_callback_service(database: DatabaseDep, config: ConfigDep) -> SignCallbackService:
    return SignCallbackService(database, config.callback_secret)


def get_signing_service(
    database: DatabaseDep,
    mynca: MyncaDep,
    fetcher: FetcherDep,
    config: ConfigDep,
) -> SigningService:
    return SigningService(
        database=database,
        mynca=mynca,
        documents=DocumentSource(config, fetcher),
        config=config,
    )


def get_download_service(
    database: DatabaseDep,
    mynca: MyncaDep,
    fetcher: FetcherDep,
    config: ConfigDep,
) -> DownloadService:
    return DownloadService(
        database=database,
        mynca=mynca,
        documents=DocumentSource(config, fetcher),
        fetcher=fetcher,
        config=config,
    )


DocumentListServiceDep = Annotated[DocumentListService, Depends(get_document_list_service)]
SignCallbackServiceDep = Annotated[SignCallbackService, Depends(get_callback_service)]
SigningServiceDep = Annotated[SigningService, Depends(get_signing_service)]
DownloadServiceDep = Annotated[DownloadService, Depends(get_download_service)]
