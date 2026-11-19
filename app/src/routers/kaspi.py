from fastapi import APIRouter

from src.features.kaspi.router import router as kaspi_feature_router

from .config import api_prefix_config

kaspi_router = APIRouter(prefix=api_prefix_config.kaspi.prefix)

kaspi_router.include_router(kaspi_feature_router)
