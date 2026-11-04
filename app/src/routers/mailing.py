from fastapi import APIRouter

from src.features.mailing.router import router as mailing_feature_router

from .config import api_prefix_config

mailing_router = APIRouter(prefix=api_prefix_config.mailing.prefix)

mailing_router.include_router(mailing_feature_router)
