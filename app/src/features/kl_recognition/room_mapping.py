"""System rooms (`room_tab`) for the rooms Composer read.

The agent only says what kind of room a label is (a closed list, `RoomType`): it reads
«Мастер спальня», «Гард.», «Кухня-ниша» better than any dictionary of spellings. The rules
that depend on several rooms and the numbering are here, the same as in
`flat_image__create_flat_image_room`: the model swaps close areas when it numbers itself.
"""

from dataclasses import dataclass
from enum import StrEnum


class RoomType(StrEnum):
    LIVING_ROOM = "Гостиная"
    BEDROOM = "Спальня"
    KITCHEN = "Кухня"
    TOILET = "Санузел"
    BATHROOM = "Ванная"
    HALLWAY = "Прихожая"
    CORRIDOR = "Коридор"
    HALL = "Холл"
    WARDROBE = "Гардеробная"
    STORAGE = "Кладовая"
    LAUNDRY = "Постирочная"
    BALCONY = "Балкон"
    OTHER = "Другое"


# Copy of the `room_tab` chains (prev_room_id), checked 23.10.2026. Bathroom and toilet chains
# stop before «с душевой» / «с радиатором»: those are not numbers. A room past the end of
# its chain goes to manual check.
CHAINS: dict[RoomType, list[tuple[int, str]]] = {
    RoomType.LIVING_ROOM: [(1, "Гостиная"), (39, "Гостиная №2"), (40, "Гостиная №3")],
    RoomType.BEDROOM: [
        (3, "Спальня"),
        (10, "Спальня №2"),
        (21, "Спальня №3"),
        (36, "Спальня №4"),
        (37, "Спальня №5"),
        (51, "Кабинет"),
        (52, "Спальня №6"),
    ],
    RoomType.KITCHEN: [(2, "Кухня"), (41, "Кухня №2"), (42, "Кухня №3")],
    RoomType.TOILET: [(5, "Санузел"), (45, "Санузел №2"), (53, "Санузел №3")],
    RoomType.BATHROOM: [(4, "Ванная"), (23, "Ванная №2"), (32, "Ванная №3"), (33, "Ванная №4")],
    RoomType.HALLWAY: [(6, "Прихожая")],
    RoomType.CORRIDOR: [(7, "Коридор"), (43, "Коридор №2"), (44, "Коридор №3")],
    RoomType.WARDROBE: [
        (22, "Гардеробная"),
        (27, "Гардеробная №2"),
        (34, "Гардеробная №3"),
        (35, "Гардеробная №4"),
        (47, "Гардеробная №5"),
        (50, "Гардеробная №6"),
    ],
    RoomType.LAUNDRY: [(46, "Постирочная"), (48, "Постирочная №2")],
    RoomType.BALCONY: [(20, "Балкон"), (25, "Балкон №2"), (28, "Балкон №3"), (29, "Балкон №4")],
}
# `flat_image_equal_room_tab` maps КЛАД to «Гардеробная», though «Кладовая» exists in room_tab.
STORAGE_AS = RoomType.WARDROBE
# `flat_image__create_flat_image_room`: a toilet this large is a bathroom.
BATHROOM_MIN_AREA = 3.5


@dataclass(frozen=True)
class SystemRoom:
    room_id: int
    name: str


# Latin letters that look like Cyrillic ones: models sometimes mix them into Russian words.
_LOOKALIKES = str.maketrans("aceopxykmthbACEOPXYKMTHB", "асеорхукмтнвАСЕОРХУКМТНВ")
_BY_NAME = {kind.value.casefold(): kind for kind in RoomType}


def room_type(value: object) -> RoomType | None:
    """The type the agent gave; None when it is missing or not from the list."""
    if value is None:
        return None
    return _BY_NAME.get(str(value).strip().translate(_LOOKALIKES).casefold())


def _resolve_types(types: list[RoomType], areas: list[float]) -> list[RoomType]:
    """Hall, storage and toilet rules that look at the other rooms of the flat."""
    resolved = [STORAGE_AS if kind is RoomType.STORAGE else kind for kind in types]

    halls = sorted(
        (i for i, kind in enumerate(resolved) if kind is RoomType.HALL), key=lambda i: -areas[i]
    )
    has_hallway = RoomType.HALLWAY in resolved
    for position, i in enumerate(halls):
        # The largest hall is the hallway when the sheet has none; the rest are corridors.
        resolved[i] = RoomType.HALLWAY if position == 0 and not has_hallway else RoomType.CORRIDOR

    toilets = [i for i, kind in enumerate(resolved) if kind is RoomType.TOILET]
    if toilets and RoomType.BATHROOM not in resolved:
        resolved[max(toilets, key=lambda i: areas[i])] = RoomType.BATHROOM
    for i in toilets:
        if areas[i] >= BATHROOM_MIN_AREA:
            resolved[i] = RoomType.BATHROOM
    return resolved


def system_rooms(types: list[RoomType], areas: list[float]) -> list[SystemRoom | None]:
    """A system room for each read room, numbered by area within its type, largest first.

    None — the type is unknown or its chain in `room_tab` has run out.
    """
    resolved = _resolve_types(types, areas)
    result: list[SystemRoom | None] = [None] * len(types)
    for kind, chain in CHAINS.items():
        same_kind = sorted(
            (i for i, value in enumerate(resolved) if value is kind), key=lambda i: -areas[i]
        )
        for position, i in enumerate(same_kind):
            if position < len(chain):
                result[i] = SystemRoom(*chain[position])
    return result
