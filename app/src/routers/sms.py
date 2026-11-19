from fastapi import APIRouter

from src.features.sms.router import router as sms_feature_router

from .config import api_prefix_config

sms_router = APIRouter(prefix=api_prefix_config.sms.prefix)

sms_router.include_router(sms_feature_router)
