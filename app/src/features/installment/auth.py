from typing import Annotated

from fastapi import Depends

from src.config import installment_auth_config
from src.security import basic_auth_dependency

InstallmentBasicAuthDep = Annotated[
    None, Depends(basic_auth_dependency(installment_auth_config, api_name="Installment"))
]
