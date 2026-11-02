from fastapi import APIRouter

from src.features.client_sign.router import router as client_sign_feature_router

from .config import api_prefix_config

client_sign_router = APIRouter(prefix=api_prefix_config.client_sign.prefix)

client_sign_router.include_router(client_sign_feature_router)
