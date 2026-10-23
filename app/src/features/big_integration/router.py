from fastapi import APIRouter

from .ddu_flat_info_multiple.router import router as ddu_flat_info_multiple_router
from .ddu_resident_agreement_status.router import (
    router as ddu_resident_agreement_status_router,
)
from .nps_results.router import router as nps_results_router
from .repair_pack_prices.router import router as repair_pack_prices_router
from .request_create_v3.router import router as request_create_v3_router
from .request_event_v3.router import router as request_event_v3_router

router = APIRouter(tags=["Big Integration"])
router.include_router(ddu_flat_info_multiple_router)
router.include_router(ddu_resident_agreement_status_router)
router.include_router(nps_results_router)
router.include_router(repair_pack_prices_router)
router.include_router(request_create_v3_router)
router.include_router(request_event_v3_router)
