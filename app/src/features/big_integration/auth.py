from typing import Annotated

from fastapi import Depends

from src.config import big_integration_auth_config
from src.security import basic_auth_dependency

BigIntegrationBasicAuthDep = Annotated[
    None, Depends(basic_auth_dependency(big_integration_auth_config, api_name="Big integration"))
]
