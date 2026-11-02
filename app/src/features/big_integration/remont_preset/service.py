from typing import TYPE_CHECKING

from fastapi import Response

from src.php_compat.text import is_php_empty
from src.repository.base import SpRows

from ..php_contract import BigIntegrationRequestError, php_success_response, php_text
from .filling import PresetFillingLoader

if TYPE_CHECKING:
    from .repo import PresetRepository

_HIDDEN_ROOM_ID = -2
_ROOM_FIELDS = ("room_id", "room_name", "room_name_kz", "room_code")
_PRICE_ROUTE_PRESET_FIELDS = ("preset_id", "preset_name", "preset_price", "preset_guid")


class RemontPresetService:
    def __init__(self, repository: PresetRepository) -> None:
        self.repository = repository

    async def remont_preset_list(self, body: dict[str, object]) -> Response:
        placement_uuid = _placement_uuid(body)
        preset_kits = await self.repository.ddu_preset_list_v2(body)
        rooms = await self.repository.render_plan_room_read(placement_uuid)
        loader = PresetFillingLoader(self.repository, placement_uuid, compact=False)
        return php_success_response(
            {
                "placementUUID": body["placementUUID"],
                "preset_list": [
                    {
                        **kit,
                        "features": await self._load_features(kit["preset_id"]),
                        "additional": await loader.load_sections(kit["preset_id"]),
                        "renders": await self._load_renders(
                            kit["preset_id"], placement_uuid, rooms
                        ),
                    }
                    for kit in preset_kits
                ],
            },
        )

    async def remont_preset(self, body: dict[str, object]) -> Response:
        placement_uuid = _placement_uuid(body)
        if is_php_empty(body.get("preset_id")):
            raise BigIntegrationRequestError("preset_id не может быть пустым")
        preset_kits = await self.repository.ddu_preset_list_v2(body)
        loader = PresetFillingLoader(self.repository, placement_uuid, compact=True)
        return php_success_response(
            {
                "placementUUID": body["placementUUID"],
                "preset_list": [
                    {
                        **{field: kit[field] for field in _PRICE_ROUTE_PRESET_FIELDS},
                        "additional": await loader.load_sections(kit["preset_id"]),
                    }
                    for kit in preset_kits
                ],
            },
        )

    async def _load_features(self, preset_kit_id: int) -> SpRows:
        groups = await self.repository.render_preset_feature_p_category_read(preset_kit_id)
        return [
            {
                **group,
                "items": await self.repository.render_preset_feature_read(
                    preset_kit_id,
                    group["feature_category_pid"],
                ),
            }
            for group in groups
        ]

    async def _load_renders(
        self,
        preset_kit_id: int,
        placement_uuid: str,
        rooms: SpRows,
    ) -> dict[str, dict[str, SpRows]]:
        clean: SpRows = []
        with_filling: SpRows = []
        for room in rooms:
            if room["room_id"] == _HIDDEN_ROOM_ID:
                continue
            head = {field: room[field] for field in _ROOM_FIELDS}
            for rooms_out, is_furniture in ((clean, 0), (with_filling, 1)):
                views = await self.repository.render_read(
                    placement_uuid,
                    room["room_id"],
                    preset_kit_id,
                    is_furniture,
                )
                rooms_out.append({**head, "views": views})
        return {"clean": {"rooms": clean}, "clean_with_filling": {"rooms": with_filling}}


def _placement_uuid(body: dict[str, object]) -> str:
    placement_uuid = body.get("placementUUID")
    if is_php_empty(placement_uuid):
        raise BigIntegrationRequestError("ГУИД помещения не может быть пустым")
    return php_text(placement_uuid)
