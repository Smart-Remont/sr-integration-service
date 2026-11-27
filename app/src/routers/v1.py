from fastapi import APIRouter

from src.features.installment.router import router as installment_router
from src.features.installment.router_bif import router as installment_bif_router
from src.features.kl_recognition.router import router as kl_recognition_router
from src.features.storage.router import router as storage_router

from .config import api_prefix_config

v1_router = APIRouter(prefix=api_prefix_config.v1.prefix)

v1_router.include_router(installment_router)
v1_router.include_router(installment_bif_router)
v1_router.include_router(storage_router)
v1_router.include_router(kl_recognition_router)
