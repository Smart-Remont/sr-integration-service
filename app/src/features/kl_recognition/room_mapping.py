"""System rooms (`room_tab`) for the rooms Composer read.

The agent only says what kind of room a label is (a closed list, `RoomType`): it reads
«Мастер спальня», «Гард.», «Кухня-ниша» better than any dictionary of spellings. The rules
that depend on several rooms and the numbering are here, the same as in
`flat_image__create_flat_image_room`: the model swaps close areas when it numbers itself.
"""

import re
from dataclasses import dataclass, field
from difflib import get_close_matches
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
# A storeroom or wardrobe behind only a dashed line is part of the room it opens into, and
# operators add its area to that room: storerooms to the kitchen (MERIDIAN 201: 11,19 + 3,16;
# 168; Arman Qala), wardrobes to the bedroom or the hallway (planirovki since 09.2025: Aisar 4
# 14,21 + 2,11; Batysmura Prime 14,32 + 4,00; Asyl Zaman 18,09 + 3,55 and hallway 3,96 + 2,67;
# Samğar, Alem Sana). Wardrobes in a «Холл» stay separate, as on MERIDIAN 1.
MERGE_TARGETS: dict[RoomType, set[RoomType]] = {
    RoomType.STORAGE: {RoomType.KITCHEN},
    RoomType.WARDROBE: {RoomType.BEDROOM, RoomType.HALLWAY},
}


# A balcony with one printed area: it is the reduced one (the sum check passes only then) and
# operators enter the full area as printed / 0,3 (Sanat: 0,99 → 3,30, 1,08 → 3,60, 1,11 → 3,70).
# For a loggia the coefficient on such sheets is not known, so it goes to manual check.
BALCONY_COEFFICIENT = 0.3
BALCONY_WORD = "балкон"


def full_balcony_area(name: str, printed: float) -> float | None:
    """Full area of a balcony printed with its reduced area only; None when it cannot be told."""
    return round(printed / BALCONY_COEFFICIENT, 2) if BALCONY_WORD in name.casefold() else None


# A terrace is not a room of the flat in the system: planirovki almost never hold one (27 of
# 52 thousand since 2025), its area is kept on the flat (`flat_tab.terrace_area`).
TERRACE_WORD = "террас"


def is_terrace(name: str) -> bool:
    return TERRACE_WORD in name.casefold()


@dataclass(frozen=True)
class SystemRoom:
    room_id: int
    name: str


@dataclass
class ReadRoom:
    name: str
    # None: the sheet prints no area for this room (the agent must not compute one).
    area: float | None
    financial_area: float | None
    kind: RoomType
    # The neighbour (name, area) the agent says this room is open to across a dashed line.
    open_to: tuple[str, float] | None = None
    merged_from: list[tuple[str, float]] = field(default_factory=list)
    # Side of the plan → neighbours (name, area) as the agent wrote them.
    neighbors: dict[str, list[tuple[str, float]]] = field(default_factory=dict)


def merge_open_storage(rooms: list[ReadRoom], doubts: list[str]) -> list[ReadRoom]:
    """Adds storerooms and wardrobes behind a dashed line to the room they open into."""
    original_areas = [room.area for room in rooms]
    absorbed: set[int] = set()
    for i, room in enumerate(rooms):
        targets = MERGE_TARGETS.get(room.kind)
        if not targets or room.open_to is None or room.area is None:
            continue
        name, area = room.open_to
        candidates = [
            j
            for j, other in enumerate(rooms)
            if j != i
            and j not in absorbed
            and original_areas[j] is not None
            and abs(original_areas[j] - area) < 0.005
        ]
        if len(candidates) > 1:
            candidates = [j for j in candidates if rooms[j].name == name] or candidates
        if (
            len(candidates) != 1
            or rooms[candidates[0]].kind not in targets
            or rooms[candidates[0]].financial_area is None
            or room.financial_area is None
        ):
            doubts.append(f"«{room.name}» {room.area} за пунктиром у «{name}» {area} не объединён")
            continue
        parent = rooms[candidates[0]]
        if not parent.merged_from:
            parent.merged_from.append((parent.name, parent.area))
        parent.merged_from.append((room.name, room.area))
        parent.area = round(parent.area + room.area, 2)
        parent.financial_area = round(parent.financial_area + room.financial_area, 2)
        for side, refs in room.neighbors.items():
            parent.neighbors.setdefault(side, []).extend(refs)
        absorbed.add(i)
    return [room for i, room in enumerate(rooms) if i not in absorbed]


# A kitchen with one of these words in its label is open to the living room by definition.
OPEN_KITCHEN_WORDS = ("ниш", "студи")


def is_kitchen_studio(rooms: list[ReadRoom]) -> bool:
    """A kitchen labelled as a niche or a studio: areas stay apart, only the flag is set
    (planirovki since 2025: «Кухня-ниша» next to «Гостиная», never summed). Needs a living room:
    `check_rooms` in myspace lets a plan without one through only with this flag.

    Only the printed label sets it: the agent's own guess was wrong on a solid wall
    (SHABYT 1, кв. 237), so a guess goes to the operator, see `note_guessed_studio`."""
    if not any(room.kind is RoomType.LIVING_ROOM for room in rooms):
        return False
    return any(room.kind is RoomType.KITCHEN and _labelled_open(room) for room in rooms)


def _labelled_open(room: ReadRoom) -> bool:
    return any(word in room.name.casefold() for word in OPEN_KITCHEN_WORDS)


def note_guessed_studio(rooms: list[ReadRoom], doubts: list[str]) -> None:
    """A kitchen the agent sees as open to the living room, though the label does not say so."""
    living = [room for room in rooms if room.kind is RoomType.LIVING_ROOM]
    for room in rooms:
        if room.kind is not RoomType.KITCHEN or _labelled_open(room) or not room.open_to:
            continue
        if any(
            other.area is not None and abs(other.area - room.open_to[1]) < 0.005 for other in living
        ):
            doubts.append(
                f"«{room.name}» {room.area}: похоже, кухня открыта в гостиную, но в подписи этого нет. "
                "Признак студии не поставлен: сверьте с листом"
            )


def note_kitchen_without_living_room(rooms: list[ReadRoom], doubts: list[str]) -> None:
    """One large «Кухня» with no «Гостиная»: operators split its area by hand."""
    if any(room.kind is RoomType.LIVING_ROOM for room in rooms):
        return
    for room in rooms:
        if room.kind is RoomType.KITCHEN:
            doubts.append(
                f"«{room.name}» {room.area}: гостиной на листе нет. Посмотрите, какая комната "
                "гостиная; если кухня и гостиная одно помещение, разделите площадь вручную"
            )


# The prompt asks for the type as its number in this list: a number has no letters to mix up.
TYPE_NUMBERS: dict[int, RoomType] = dict(enumerate(RoomType, start=1))

# Latin letters models mix into Russian words. Both lookalikes («Гоcтиная») and transliteration
# («spальня», «Мастer», «Гостinая»: SHABYT 1, Impulse, MERIDIAN 1), so one Latin letter can stand
# for several Cyrillic ones (p: р by look, п by sound). Types come as numbers; this is for names.
_LATIN_AS: dict[str, str] = {
    "a": "а", "b": "бв", "c": "сц", "d": "д", "e": "еэ", "f": "ф", "g": "г", "h": "нх",
    "i": "иі", "k": "к", "l": "л", "m": "м", "n": "нп", "o": "о", "p": "рп", "r": "рг",
    "s": "с", "t": "т", "u": "уи", "v": "в", "x": "х", "y": "уы", "z": "з",
}  # fmt: skip
# Words of room labels on BI sheets (casefolded).
ROOM_WORDS = (
    "гостиная", "спальня", "мастер", "мастер-спальня", "детская", "гостевая", "кабинет", "кухня",
    "кухня-ниша", "кухня-гостиная", "ниша", "столовая", "санузел", "с/у", "ванная", "душевая",
    "прихожая", "коридор", "холл", "тамбур", "гардероб", "гардеробная", "гард.", "кладовая",
    "клад.", "постирочная", "постир.", "лоджия", "балкон", "терраса", "игровая", "зона",
    "с", "для", "шкафа", "кухней-нишей", "кухней", "нишей", "гостиная-столовая", "зал",
)  # fmt: skip
_CYRILLIC = re.compile("[а-яё]", re.IGNORECASE)
_LATIN = re.compile("[a-z]", re.IGNORECASE)
_BY_NAME = {kind.value.casefold(): kind for kind in RoomType}
TYPE_MATCH_CUTOFF = 0.85


def _fits(word: str, known: str) -> bool:
    return len(word) == len(known) and all(
        a == b or b in _LATIN_AS.get(a, "") for a, b in zip(word, known, strict=True)
    )


def _russian_word(word: str) -> str:
    if not (_CYRILLIC.search(word) and _LATIN.search(word)):
        return word
    folded = word.casefold()
    hits = [known for known in ROOM_WORDS if _fits(folded, known)]
    if len(hits) != 1:
        # not a label word: only the letters that cannot be anything else
        return "".join(
            _LATIN_AS[c.casefold()][0] if len(_LATIN_AS.get(c.casefold(), "")) == 1 else c
            for c in word
        )
    return hits[0].capitalize() if word[0].isupper() else hits[0]


def russian_name(name: str) -> str:
    """A Russian label with stray Latin letters made Cyrillic; «WC», «TV-Зона» and the like stay."""
    return " ".join(
        "-".join(_russian_word(part) for part in word.split("-")) for word in name.split(" ")
    )


def room_type(value: object) -> RoomType | None:
    """The type the agent gave: its number in the list or, from older answers, the word.
    None when it is missing or not from the list."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float) or str(value).strip().isdigit():
        number = float(value) if isinstance(value, int | float) else int(str(value).strip())
        return TYPE_NUMBERS.get(int(number)) if number == int(number) else None
    key = russian_name(str(value).strip()).casefold()
    if key in _BY_NAME:
        return _BY_NAME[key]
    # one stray Latin letter («Gостиная»); a different word stays unknown
    close = get_close_matches(key, _BY_NAME, n=1, cutoff=TYPE_MATCH_CUTOFF)
    return _BY_NAME[close[0]] if close else None


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

    # room_tab has one hallway; a second «Прихожая» on the sheet operators enter as a corridor
    # (Family Gardens 1, кв. 129: 6,94 — прихожая, 5,53 — коридор).
    hallways = sorted(
        (i for i, kind in enumerate(resolved) if kind is RoomType.HALLWAY), key=lambda i: -areas[i]
    )
    for i in hallways[1:]:
        resolved[i] = RoomType.CORRIDOR

    # No hallway on the sheet: operators enter the only corridor as the hallway (468 of 481
    # planirovki whose sheet has «Коридор» and neither «Прихожая» nor «Холл»). With several
    # corridors they pick the smaller one 46 times and the larger 25 — no rule, left as is.
    corridors = [i for i, kind in enumerate(resolved) if kind is RoomType.CORRIDOR]
    if RoomType.HALLWAY not in resolved and len(corridors) == 1:
        resolved[corridors[0]] = RoomType.HALLWAY

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
