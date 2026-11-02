from fastapi import APIRouter

from src.features.showroom.router import router as showroom_feature_router

from .config import api_prefix_config

showroom_router = APIRouter(prefix=api_prefix_config.showroom.prefix)

showroom_router.include_router(showroom_feature_router)
