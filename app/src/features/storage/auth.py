from typing import Annotated

from fastapi import Depends

from src.config import storage_auth_config
from src.security import basic_auth_dependency

StorageBasicAuthDep = Annotated[
    None, Depends(basic_auth_dependency(storage_auth_config, api_name="Storage"))
]
