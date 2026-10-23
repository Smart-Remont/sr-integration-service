from typing import Annotated

from fastapi import Depends

from src.config import kl_recognition_config

from .client import BiLoadSheetClient
from .composer import ComposerClient
from .plan_images import build_plan_images
from .service import KlRecognitionService


def get_kl_recognition_service() -> KlRecognitionService:
    return KlRecognitionService(
        sheet_client=BiLoadSheetClient(kl_recognition_config),
        composer=ComposerClient(kl_recognition_config),
        build_images=build_plan_images,
        model=kl_recognition_config.cursor_model,
    )


KlRecognitionServiceDep = Annotated[KlRecognitionService, Depends(get_kl_recognition_service)]
