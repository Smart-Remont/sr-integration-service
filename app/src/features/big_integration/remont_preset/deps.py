from typing import Annotated

from fastapi import Depends

from src.database.deps import DatabaseConnectionDep

from .repo import PresetRepository
from .service import RemontPresetService


def get_remont_preset_service(connection: DatabaseConnectionDep) -> RemontPresetService:
    return RemontPresetService(repository=PresetRepository(connection=connection))


RemontPresetServiceDep = Annotated[RemontPresetService, Depends(get_remont_preset_service)]
