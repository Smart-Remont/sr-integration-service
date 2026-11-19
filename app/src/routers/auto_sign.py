from fastapi import APIRouter

from src.features.auto_sign.router import router as auto_sign_feature_router

from .config import api_prefix_config

auto_sign_router = APIRouter(prefix=api_prefix_config.auto_sign.prefix)

auto_sign_router.include_router(auto_sign_feature_router)
