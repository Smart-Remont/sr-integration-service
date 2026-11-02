from fastapi import APIRouter

from src.features.albato_meta.router import router as albato_meta_feature_router

from .config import api_prefix_config

albato_meta_router = APIRouter(prefix=api_prefix_config.albato_meta.prefix)

albato_meta_router.include_router(albato_meta_feature_router)
