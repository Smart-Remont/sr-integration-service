from src.php_compat.json_codec import php_json_encode_default
from src.repository.base import SpRows

from ..sp_reader import BigIntegrationSpReader


class PresetRepository(BigIntegrationSpReader):
    async def ddu_preset_list_v2(self, payload: dict[str, object]) -> SpRows:
        return await self.read_rows("rest.ddu__preset_list_v2", php_json_encode_default(payload))

    async def render_plan_room_read(self, flat_guid: str) -> SpRows:
        return await self.read_rows("rest.render_plan_room__read", flat_guid)

    async def render_preset_feature_p_category_read(self, preset_kit_id: int) -> SpRows:
        return await self.read_rows("rest.render_preset_feature_p_category__read", preset_kit_id)

    async def render_preset_feature_read(self, preset_kit_id: int, category_id: int) -> SpRows:
        return await self.read_rows("rest.render_preset_feature__read", preset_kit_id, category_id)

    async def render_filling_section_read(self, preset_kit_id: int) -> SpRows:
        return await self.read_rows("rest.render_filling__section__read", preset_kit_id)

    async def render_filling_room_read(
        self,
        flat_guid: str,
        preset_kit_id: int,
        section_id: int,
    ) -> SpRows:
        return await self.read_rows(
            "rest.render_filling__room_read",
            flat_guid,
            preset_kit_id,
            section_id,
        )

    async def render_filling_work_set_read(
        self,
        preset_kit_id: int,
        section_id: int,
        room_id: int | None,
    ) -> SpRows:
        return await self.read_rows(
            "rest.render_filling__work_set_read",
            preset_kit_id,
            section_id,
            room_id,
        )

    async def render_filling_material_set_read(
        self,
        preset_kit_id: int,
        section_id: int,
        room_id: int | None,
        work_set_id: int | None,
        flat_guid: str,
    ) -> SpRows:
        return await self.read_rows(
            "rest.render_filling__material_set_read",
            preset_kit_id,
            section_id,
            room_id,
            work_set_id,
            flat_guid,
        )

    async def render_filling_features_read(self, preset_material_id: int) -> SpRows:
        return await self.read_rows("rest.render_filling_features__read", preset_material_id)

    async def render_filling_set_item_read(self, preset_material_id: int) -> SpRows:
        return await self.read_rows("rest.render_filling__set_item_read", preset_material_id)

    async def render_set_photo_read(self, material_set_id: int) -> SpRows:
        return await self.read_rows("rest.render_set_photo__read", material_set_id)

    async def render_read(
        self,
        flat_guid: str,
        room_id: int,
        preset_kit_id: int,
        is_furniture: int,
    ) -> SpRows:
        return await self.read_rows(
            "rest.render__read",
            flat_guid,
            room_id,
            preset_kit_id,
            is_furniture,
        )
