from fastapi import APIRouter

from src.features.buh_1c.router import router as buh_1c_feature_router

from .config import api_prefix_config

buh_1c_router = APIRouter(prefix=api_prefix_config.buh_1c.prefix)

buh_1c_router.include_router(buh_1c_feature_router)
