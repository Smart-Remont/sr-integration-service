from typing import TYPE_CHECKING

from fastapi import status
from fastapi.responses import JSONResponse

from ..errors import BigIntegrationDatabaseError
from ..responses import big_integration_error_response, big_integration_success_response

if TYPE_CHECKING:
    from .repo import RepairPackPricesRepository

_MAX_PLACEMENT_UUIDS = 10_000
_NOT_FOUND_ERROR = "Подходящий пакет не найден"


class RepairPackPricesService:
    def __init__(self, repository: RepairPackPricesRepository) -> None:
        self.repository = repository

    async def repair_pack_prices(self, body: dict[str, object]) -> JSONResponse:
        placement_uuids_raw = body.get("placementUUIDs")
        if placement_uuids_raw is None:
            return big_integration_success_response([])
        if not isinstance(placement_uuids_raw, list):
            return big_integration_error_response(
                'Поле "placementUUIDs" имеет некорректный формат',
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        if len(placement_uuids_raw) > _MAX_PLACEMENT_UUIDS:
            return big_integration_error_response(
                'Слишком много элементов в поле "placementUUIDs"',
                status_code=status.HTTP_403_FORBIDDEN,
            )

        results: list[dict[str, object]] = []
        for placement_uuid_raw in placement_uuids_raw:
            placement_uuid = str(placement_uuid_raw)
            try:
                row = await self.repository.ddu_repair_pack_info_get(placement_uuid)
            except BigIntegrationDatabaseError as exc:
                results.append(
                    _error_response_row(placement_uuid=placement_uuid, message=exc.message),
                )
                continue

            if row is None:
                results.append(
                    _error_response_row(
                        placement_uuid=placement_uuid,
                        message=_NOT_FOUND_ERROR,
                    ),
                )
                continue

            results.append(
                {
                    **row,
                    "placementUUID": placement_uuid,
                    "status": True,
                    "error": None,
                },
            )

        return big_integration_success_response(results)


def _error_response_row(placement_uuid: str, message: str) -> dict[str, object]:
    return {
        "placementUUID": placement_uuid,
        "preset_guid": None,
        "preset_name": None,
        "is_old_repair_package": None,
        "preset_price": None,
        "optimal_price": None,
        "optimal_preset_guid": None,
        "optimal_preset_name": None,
        "status": False,
        "error": message,
    }
