"""MySpace page «Рассрочки»: applications of both banks and their totals."""

from datetime import date
from typing import Any

from .repo import InstallmentRepository
from .schemas import (
    InstallmentOAApplication,
    InstallmentOAApplicationListResponse,
    InstallmentStatsResponse,
)


class InstallmentOAService:
    def __init__(self, repository: InstallmentRepository) -> None:
        self.repository = repository

    async def list_applications(
        self,
        *,
        provider_code: str | None,
        status: str | None,
        date_from: date | None,
        date_to: date | None,
        client_request_id: int | None,
        search: str | None,
        limit: int,
        offset: int,
    ) -> InstallmentOAApplicationListResponse:
        filters = _filters(
            provider_code=provider_code,
            status=status,
            date_from=date_from,
            date_to=date_to,
            client_request_id=client_request_id,
            search=search,
            limit=limit,
            offset=offset,
        )
        rows = await self.repository.list_applications_for_oa(filters)
        total = int(rows[0]["total_count"]) if rows else 0
        if not rows and offset > 0:
            # Past the last page: the count still comes from the first one.
            first = await self.repository.list_applications_for_oa(
                {**filters, "offset": 0, "limit": 1}
            )
            total = int(first[0]["total_count"]) if first else 0
        return InstallmentOAApplicationListResponse(
            items=[InstallmentOAApplication.model_validate(row) for row in rows],
            total=total,
            limit=limit,
            offset=offset,
        )

    async def stats(
        self,
        *,
        provider_code: str | None,
        date_from: date | None,
        date_to: date | None,
    ) -> InstallmentStatsResponse:
        filters = _filters(provider_code=provider_code, date_from=date_from, date_to=date_to)
        return InstallmentStatsResponse.model_validate(await self.repository.get_oa_stats(filters))


def _filters(**values: Any) -> dict[str, Any]:
    return {
        key: value.isoformat() if isinstance(value, date) else value
        for key, value in values.items()
        if value is not None and value != ""
    }
