from fastapi import APIRouter

from src.features.big_integration.router import router as big_integration_feature_router

from .config import api_prefix_config

big_integration_router = APIRouter(prefix=api_prefix_config.big_integration.prefix)

big_integration_router.include_router(big_integration_feature_router)
