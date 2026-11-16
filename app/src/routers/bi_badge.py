from fastapi import APIRouter

from src.features.bi_badge.router import router as bi_badge_feature_router

from .config import api_prefix_config

bi_badge_router = APIRouter(prefix=api_prefix_config.bi_badge.prefix)

bi_badge_router.include_router(bi_badge_feature_router)
