from typing import Annotated

from fastapi import Depends

from src.config import bi_badge_config
from src.database.deps import CommitBeforeResponseConnectionDep

from .repo import BiBadgeRepository
from .service import BiBadgeService


def get_bi_badge_service(connection: CommitBeforeResponseConnectionDep) -> BiBadgeService:
    return BiBadgeService(
        repository=BiBadgeRepository(connection=connection),
        ip_secret=bi_badge_config.ip_secret,
    )


BiBadgeServiceDep = Annotated[BiBadgeService, Depends(get_bi_badge_service)]
