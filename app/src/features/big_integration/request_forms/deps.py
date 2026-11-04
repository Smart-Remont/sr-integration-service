from typing import Annotated

from fastapi import Depends

from src.database.deps import CommitBeforeResponseConnectionDep

from .repo import RequestFormsRepository
from .service import RequestFormsService


def get_request_forms_service(
    connection: CommitBeforeResponseConnectionDep,
) -> RequestFormsService:
    return RequestFormsService(repository=RequestFormsRepository(connection=connection))


RequestFormsServiceDep = Annotated[RequestFormsService, Depends(get_request_forms_service)]
