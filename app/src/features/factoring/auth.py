from typing import Annotated

from fastapi import Depends

from src.config import factoring_auth_config
from src.security import basic_auth_dependency

FactoringBasicAuthDep = Annotated[
    None, Depends(basic_auth_dependency(factoring_auth_config, api_name="Factoring"))
]
