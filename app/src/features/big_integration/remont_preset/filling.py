"""Filling of a preset, section by section, as production PHP builds it.

The list route gives each material set its features, items and photos. The price route gives
only its name and price, and cuts the fields of a room-and-work-set section down as well.

Production PHP keeps $additional and $work_set between sections and presets, and BI Group gets
the result: a work-set section holds only the last work set, or the one left from before when
it has none (null if there was none yet). The loader reproduces this on purpose.
"""

from src.repository.base import SpRow

from .repo import PresetRepository

_ROOM_WORK_SET = "CONSTRUCTOR_TAB_ROOM_WORK_SET"
_ROOM = "CONSTRUCTOR_TAB_ROOM"
_WORK_SET = "CONSTRUCTOR_TAB_WORK_SET"
_PLAIN_SECTIONS = ("CONSTRUCTOR_TAB", "FILLING")
_COMPACT_SECTION_FIELDS = (
    "section_id",
    "section_name",
    "section_name_kz",
    "section_show_type_code",
)
_COMPACT_MATERIAL_FIELDS = ("preset_material_id", "set_name", "set_price")


class PresetFillingLoader:
    """One per request: a section can depend on the sections and presets built before it."""

    def __init__(self, repository: PresetRepository, placement_uuid: str, *, compact: bool) -> None:
        self._repository = repository
        self._placement_uuid = placement_uuid
        self._compact = compact
        self._previous_section: SpRow = {}
        self._last_work_set: SpRow | None = None

    async def load_sections(self, preset_kit_id: int) -> list[SpRow]:
        sections = await self._repository.render_filling_section_read(preset_kit_id)
        loaded: list[SpRow] = []
        for section in sections:
            code = section["section_show_type_code"]
            section_id = section["section_id"]
            if code == _ROOM_WORK_SET:
                head = self._section_head(section)
                rooms = await self._load_rooms_with_work_sets(preset_kit_id, section_id)
                built = {**head, "rooms": rooms}
            elif code == _ROOM:
                rooms = await self._load_rooms_with_materials(preset_kit_id, section_id)
                built = {**section, "rooms": rooms}
            elif code == _WORK_SET:
                await self._load_work_sets(preset_kit_id, section_id, room_id=None)
                built = {**section, "work_sets": [self._last_work_set]}
            elif code in _PLAIN_SECTIONS:
                materials = await self._load_materials(preset_kit_id, section_id, None, None)
                built = {**section, "sets": materials}
            else:
                continue
            self._previous_section = built
            loaded.append(built)
        return loaded

    def _section_head(self, section: SpRow) -> SpRow:
        if not self._compact:
            return section
        fields = {field: section[field] for field in _COMPACT_SECTION_FIELDS}
        return {**self._previous_section, **fields}

    async def _load_rooms_with_work_sets(self, preset_kit_id: int, section_id: int) -> list[SpRow]:
        rooms = await self._repository.render_filling_room_read(
            self._placement_uuid,
            preset_kit_id,
            section_id,
        )
        return [
            {
                **room,
                "work_sets": await self._load_work_sets(
                    preset_kit_id,
                    section_id,
                    room["room_id"],
                ),
            }
            for room in rooms
        ]

    async def _load_rooms_with_materials(self, preset_kit_id: int, section_id: int) -> list[SpRow]:
        rooms = await self._repository.render_filling_room_read(
            self._placement_uuid,
            preset_kit_id,
            section_id,
        )
        loaded: list[SpRow] = []
        for room in rooms:
            self._last_work_set = {**(self._last_work_set or {}), "sets": []}
            materials = await self._load_materials(
                preset_kit_id,
                section_id,
                room["room_id"],
                work_set_id=None,
            )
            loaded.append({**room, "sets": materials})
        return loaded

    async def _load_work_sets(
        self,
        preset_kit_id: int,
        section_id: int,
        room_id: int | None,
    ) -> list[SpRow]:
        work_sets = await self._repository.render_filling_work_set_read(
            preset_kit_id,
            section_id,
            room_id,
        )
        loaded: list[SpRow] = []
        for work_set in work_sets:
            materials = await self._load_materials(
                preset_kit_id,
                section_id,
                room_id,
                work_set["work_set_id"],
            )
            self._last_work_set = {**work_set, "sets": materials}
            loaded.append(self._last_work_set)
        return loaded

    async def _load_materials(
        self,
        preset_kit_id: int,
        section_id: int,
        room_id: int | None,
        work_set_id: int | None,
    ) -> list[SpRow]:
        materials = await self._repository.render_filling_material_set_read(
            preset_kit_id,
            section_id,
            room_id,
            work_set_id,
            self._placement_uuid,
        )
        if self._compact:
            return [
                {field: material[field] for field in _COMPACT_MATERIAL_FIELDS}
                for material in materials
            ]
        return [await self._with_details(material) for material in materials]

    async def _with_details(self, material: SpRow) -> SpRow:
        details = {key: value for key, value in material.items() if key != "material_set_id"}
        preset_material_id = material["preset_material_id"]
        return {
            **details,
            "features": await self._repository.render_filling_features_read(preset_material_id),
            "items": await self._repository.render_filling_set_item_read(preset_material_id),
            "photos": await self._repository.render_set_photo_read(material["material_set_id"]),
        }
