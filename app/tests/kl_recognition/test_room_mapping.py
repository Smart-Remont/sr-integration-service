"""System rooms for the read rooms: the rules of `flat_image__create_flat_image_room` and
numbering by area, which the model got wrong when it numbered rooms itself."""

from src.features.kl_recognition.room_mapping import (
    ReadRoom,
    RoomType,
    merge_open_storage,
    room_type,
    system_rooms,
)

T = RoomType


def names(types: list[RoomType], areas: list[float]) -> list[str | None]:
    return [room.name if room else None for room in system_rooms(types, areas)]


def test_bedrooms_are_numbered_by_area_largest_first() -> None:
    # Close areas the model once swapped; the order on the sheet does not matter.
    assert names([T.BEDROOM, T.BEDROOM, T.BEDROOM], [14.08, 14.46, 17.2]) == [
        "Спальня №3",
        "Спальня №2",
        "Спальня",
    ]


def test_sixth_bedroom_follows_the_room_tab_chain() -> None:
    areas = [20.0, 19.0, 18.0, 17.0, 16.0, 15.0, 14.0]
    assert names([T.BEDROOM] * 7, areas)[5:] == ["Кабинет", "Спальня №6"]


def test_room_past_the_end_of_its_chain_is_left_unmapped() -> None:
    assert names([T.CORRIDOR] * 4, [5.0, 4.0, 3.0, 2.0]) == [
        "Коридор",
        "Коридор №2",
        "Коридор №3",
        None,
    ]


def test_hall_is_hallway_without_one_and_corridor_with_one() -> None:
    assert names([T.HALL, T.KITCHEN], [8.0, 10.0]) == ["Прихожая", "Кухня"]
    assert names([T.HALL, T.HALLWAY], [8.0, 4.0]) == ["Коридор", "Прихожая"]
    assert names([T.HALL, T.HALL], [3.0, 8.0]) == ["Коридор", "Прихожая"]


def test_second_hallway_is_a_corridor() -> None:
    assert names([T.HALLWAY, T.HALL, T.HALLWAY], [6.94, 16.43, 5.53]) == [
        "Прихожая",
        "Коридор",
        "Коридор №2",
    ]


def test_largest_toilet_is_bathroom_when_there_is_none() -> None:
    assert names([T.TOILET, T.TOILET], [2.1, 3.2]) == ["Санузел", "Ванная"]


def test_large_toilet_is_bathroom() -> None:
    assert names([T.BATHROOM, T.TOILET, T.TOILET], [5.0, 3.5, 2.0]) == [
        "Ванная",
        "Ванная №2",
        "Санузел",
    ]


def test_storage_is_a_wardrobe_as_in_the_ocr_dictionary() -> None:
    assert names([T.STORAGE, T.WARDROBE], [3.16, 2.18]) == ["Гардеробная", "Гардеробная №2"]


def test_type_is_read_leniently_but_only_from_the_list() -> None:
    assert room_type(" спальня ") is T.BEDROOM
    assert room_type("Caнузел") is T.TOILET  # Latin «C» and «a»
    assert room_type("Сауна") is None
    assert room_type(None) is None
    assert names([T.OTHER], [10.0]) == [None]


def storage(name: str, area: float, open_to: tuple[str, float] | None) -> ReadRoom:
    return ReadRoom(name, area, area, T.STORAGE, open_to)


def test_storage_behind_a_dashed_line_is_added_to_the_kitchen() -> None:
    kitchen = ReadRoom("Кухня", 11.19, 11.19, T.KITCHEN)
    doubts: list[str] = []
    rooms = merge_open_storage([kitchen, storage("Кладовая", 3.16, ("Кухня", 11.19))], doubts)
    assert [(r.name, r.area) for r in rooms] == [("Кухня", 14.35)]
    assert rooms[0].merged_from == [("Кухня", 11.19), ("Кладовая", 3.16)]
    assert doubts == []


def test_two_storerooms_into_one_kitchen_match_its_printed_area() -> None:
    kitchen = ReadRoom("Кухня", 9.0, 9.0, T.KITCHEN)
    rooms = merge_open_storage(
        [kitchen, storage("Клад.", 2.0, ("Кухня", 9.0)), storage("Кладовая", 1.0, ("Кухня", 9.0))],
        [],
    )
    assert [(r.name, r.area) for r in rooms] == [("Кухня", 12.0)]


def test_wardrobe_behind_a_dashed_line_is_added_to_the_bedroom_or_hallway() -> None:
    # As operators enter it since 09.2025: Aisar 4 bedroom 16,32 = 14,21 + 2,11.
    bedroom = ReadRoom("Спальня", 14.21, 14.21, T.BEDROOM)
    hallway = ReadRoom("Прихожая", 3.96, 3.96, T.HALLWAY)
    rooms = merge_open_storage(
        [
            bedroom,
            hallway,
            ReadRoom("Гард.", 2.11, 2.11, T.WARDROBE, ("Спальня", 14.21)),
            ReadRoom("Гард.", 2.67, 2.67, T.WARDROBE, ("Прихожая", 3.96)),
        ],
        [],
    )
    assert [(r.name, r.area) for r in rooms] == [("Спальня", 16.32), ("Прихожая", 6.63)]


def test_wardrobe_open_to_a_hall_or_storeroom_open_to_a_bedroom_stays_separate() -> None:
    # MERIDIAN 1 keeps the wardrobe in a «Холл» apart; a storeroom joins only the kitchen.
    hall = ReadRoom("Холл", 14.53, 14.53, T.HALL)
    bedroom = ReadRoom("Спальня", 15.0, 15.0, T.BEDROOM)
    rooms = merge_open_storage(
        [
            hall,
            bedroom,
            ReadRoom("Гардероб", 2.18, 2.18, T.WARDROBE, ("Холл", 14.53)),
            storage("Клад.", 2.0, ("Спальня", 15.0)),
        ],
        [],
    )
    assert len(rooms) == 4


def test_storage_behind_a_wall_or_open_to_a_corridor_stays_separate() -> None:
    corridor = ReadRoom("Коридор", 7.0, 7.0, T.CORRIDOR)
    doubts: list[str] = []
    rooms = merge_open_storage(
        [corridor, storage("Клад.", 2.0, None), storage("Кладовка", 3.0, ("Коридор", 7.0))], doubts
    )
    assert len(rooms) == 3
    assert doubts == ["«Кладовка» 3.0 за пунктиром у «Коридор» 7.0 не объединён"]


def test_storage_open_to_an_unknown_room_stays_separate() -> None:
    doubts: list[str] = []
    rooms = merge_open_storage([storage("Клад.", 2.0, ("Кухня", 9.99))], doubts)
    assert len(rooms) == 1
    assert len(doubts) == 1
