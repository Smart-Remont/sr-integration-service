from typing import Annotated

from fastapi import Depends

from src.config import kl_recognition_auth_config
from src.security import basic_auth_dependency

KlRecognitionBasicAuthDep = Annotated[
    None, Depends(basic_auth_dependency(kl_recognition_auth_config, api_name="KL recognition"))
]
