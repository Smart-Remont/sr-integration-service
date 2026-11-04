from typing import TYPE_CHECKING

from fastapi import Response

from src.php_compat.json_codec import php_json_decode
from src.repository.base import SpRow

from ..php_contract import php_success_response

if TYPE_CHECKING:
    from .repo import RequestConstructivesRepository


class RequestConstructivesService:
    def __init__(self, repository: RequestConstructivesRepository) -> None:
        self.repository = repository

    async def request_constructives(self, payload: object) -> Response:
        materials = await self.repository.ddu_client_material_read(payload)
        fillings = await self.repository.ddu_client_filling_read(payload)
        request_row = await self.repository.ddu_request_full_info(payload) or {}

        return php_success_response(
            {
                "placementUUID": request_row.get("flat_guid"),
                "preset_id": request_row.get("preset_kit_id"),
                "preset_name": request_row.get("preset_kit_name"),
                "material_list": materials,
                "filling_list": [_with_decoded_characteristics(row) for row in fillings],
            },
        )


def _with_decoded_characteristics(filling: SpRow) -> SpRow:
    characteristics = filling.get("characteristic_json")
    if not isinstance(characteristics, str):
        return filling
    return {**filling, "characteristic_json": php_json_decode(characteristics)}
