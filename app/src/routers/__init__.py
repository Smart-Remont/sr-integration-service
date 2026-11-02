__all__ = ("main_router",)

from fastapi import APIRouter

from .auto_sign import auto_sign_router
from .big_integration import big_integration_router
from .client_sign import client_sign_router
from .config import api_prefix_config
from .kaspi import kaspi_router
from .sms import sms_router
from .v1 import v1_router

main_router = APIRouter(prefix=api_prefix_config.prefix)

main_router.include_router(v1_router)
main_router.include_router(big_integration_router)
main_router.include_router(kaspi_router)
main_router.include_router(sms_router)
main_router.include_router(client_sign_router)
main_router.include_router(auto_sign_router)
