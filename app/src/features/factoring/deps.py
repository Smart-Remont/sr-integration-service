from typing import Annotated

from fastapi import Depends

from src.config import app_config, factoring_prescoring_config, mynca_config
from src.database.deps import DatabaseConnectionDep

from .ff.client import FactoringClient
from .ff.mynca import MyncaClient
from .ff.repo import FactoringRepository
from .ff.service import FactoringService


def get_factoring_service(connection: DatabaseConnectionDep) -> FactoringService:
    repository = FactoringRepository(connection=connection)
    client = FactoringClient()
    mynca = MyncaClient(base_url=mynca_config.base_url, token=mynca_config.token)
    return FactoringService(
        repository=repository,
        client=client,
        mynca=mynca,
        app_env=app_config.env,
        office_public_url=mynca_config.office_public_url,
        public_base_url=mynca_config.public_base_url,
        nca_master_key=mynca_config.nca_master_key,
        prescoring_username=factoring_prescoring_config.username,
        prescoring_password=factoring_prescoring_config.password,
    )


FactoringServiceDep = Annotated[FactoringService, Depends(get_factoring_service)]
