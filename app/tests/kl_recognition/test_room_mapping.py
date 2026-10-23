"""System rooms for the read rooms: the rules of `flat_image__create_flat_image_room` and
numbering by area, which the model got wrong when it numbered rooms itself."""

from src.features.kl_recognition.room_mapping import RoomType, room_type, system_rooms

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
