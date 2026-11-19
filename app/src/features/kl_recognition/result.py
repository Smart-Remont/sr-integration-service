"""Turns Composer's answer into the API result. The sums and the status are computed here: the
agent only copies what is printed. Every area must come with the text it was copied from
(`areaText`); an area that is not in its quote, or has none, is a problem, not a result — that is
how a number fitted to the total (SHABYT 1, кв. 140: a loggia printed without areas) is caught."""

import json
import re
from typing import Any

from .room_mapping import (
    ReadRoom,
    RoomType,
    full_balcony_area,
    is_kitchen_studio,
    is_terrace,
    merge_open_storage,
    note_guessed_studio,
    note_kitchen_without_living_room,
    room_type,
    russian_name,
    system_rooms,
)
from .schemas import (
    KlChecks,
    KlProblem,
    KlProblemCode,
    KlRecognitionMeta,
    KlRecognitionResult,
    KlRoom,
    KlRoomNeighbors,
    KlRoomPart,
    KlRoomRef,
    RecognitionStatus,
)

# Sheet areas are rounded to hundredths; correctly read sheets differ from the total by 0–0.03.
SUM_TOLERANCE = 0.03
NEIGHBOR_SIDES = ("left", "right", "up", "down")
# Numbers in a printed area: «2,51 (1,26)», «15.35 м2», «6».
_QUOTE_NUMBER = re.compile(r"(?<![\d.,])(\d{1,4})(?:\s?[.,]\s?(\d{1,2}))?(?![\d])")


def quote_numbers(text: str | None) -> list[float]:
    """The numbers of a printed area, without the «2» of «м2» / «м²»."""
    cleaned = re.sub(r"[мm]\s?[2²]", " ", text or "", flags=re.IGNORECASE)
    return [float(f"{whole}.{part or 0}") for whole, part in _QUOTE_NUMBER.findall(cleaned)]


def first_json_object(text: str) -> dict[str, Any] | None:
    """The first complete JSON object in the answer (the agent may write text around it)."""
    for match in re.finditer(r"\{", text or ""):
        try:
            value = json.JSONDecoder().raw_decode(text, match.start())[0]
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None


def not_recognized_result(
    reason: str, bi_area: float | None, meta: KlRecognitionMeta
) -> KlRecognitionResult:
    return KlRecognitionResult(
        status=RecognitionStatus.NOT_RECOGNIZED,
        reason=reason,
        rooms=[],
        total_area=None,
        total_rooms=None,
        checks=KlChecks(bi_area=bi_area),
        doubts=[],
        meta=meta,
    )


def _as_float(value: Any) -> float | None:
    try:
        return round(float(value), 2) if value is not None else None
    except TypeError, ValueError:
        return None


def _in_quote(value: float | None, numbers: list[float]) -> bool:
    return value is None or any(abs(value - number) < 0.005 for number in numbers)


def _quote_problems(read: list[ReadRoom], quotes: dict[int, str | None], sheet_total: float | None):
    """An area must be printed in its quote. The rest of the sum is checked by the totals; this is
    about a number the agent wrote without it being on the sheet."""
    unbacked = [
        room
        for room in read
        if room.area is not None
        and not (
            quote_numbers(quotes.get(id(room)))
            and _in_quote(room.area, quote_numbers(quotes.get(id(room))))
            and _in_quote(room.financial_area, quote_numbers(quotes.get(id(room))))
        )
    ]
    problems = []
    for room in read:
        quote = quotes.get(id(room))
        if room.area is None:
            problems.append(
                KlProblem(
                    code=KlProblemCode.AREA_NOT_PRINTED,
                    room=room.name,
                    area=room.area,
                    text=f"у «{room.name}» на листе не найдена площадь: введите её по листу",
                )
            )
            continue
        numbers = quote_numbers(quote)
        if numbers and _in_quote(room.area, numbers) and _in_quote(room.financial_area, numbers):
            continue
        fitted = ""
        # On a sheet read right every room closes the gap, so this only says something about
        # the one room whose number is not on the sheet.
        if sheet_total is not None and len(unbacked) == 1:
            others = sum(other.financial_area or 0 for other in read if other is not room)
            if abs(sheet_total - others - (room.financial_area or 0)) < 0.015:
                fitted = " Число ровно закрывает разницу с общей площадью — похоже на подгонку."
        shown = (
            room.area
            if room.financial_area in (None, room.area)
            else f"{room.area} ({room.financial_area})"
        )
        if numbers:
            problems.append(
                KlProblem(
                    code=KlProblemCode.AREA_NOT_IN_QUOTE,
                    room=room.name,
                    area=room.area,
                    text=f"у «{room.name}» площадь {shown}, а на листе напечатано «{quote}».{fitted}",
                )
            )
        else:
            problems.append(
                KlProblem(
                    code=KlProblemCode.AREA_WITHOUT_QUOTE,
                    room=room.name,
                    area=room.area,
                    text=f"у «{room.name}» площадь {shown} не подтверждена текстом с листа: сверьте.{fitted}",
                )
            )
    return problems


def _open_to(raw: Any) -> tuple[str, float] | None:
    area = _as_float(raw.get("area")) if isinstance(raw, dict) else None
    return None if area is None else (str(raw.get("name") or "").strip(), area)


def _raw_neighbors(raw: Any) -> dict[str, list[tuple[str, float]]]:
    """The agent's neighbours by side. Anything malformed is dropped: the layout is an extra and
    must not fail a sheet whose rooms and sums are right."""
    neighbors: dict[str, list[tuple[str, float]]] = {}
    for side in NEIGHBOR_SIDES:
        refs = raw.get(side) if isinstance(raw, dict) else None
        found = [_open_to(ref) for ref in refs] if isinstance(refs, list) else []
        neighbors[side] = [ref for ref in found if ref is not None]
    return neighbors


def _neighbors(
    room: ReadRoom,
    printed_areas: dict[int, float],
    read: list[ReadRoom],
    targets: list[Any],
    terraces: list[ReadRoom],
) -> KlRoomNeighbors:
    """Resolves the agent's references to the final rooms. A room may have been merged or got its
    full area, so it is looked up by every (name, area) it was read under; a reference to nothing,
    to itself or to several rooms is left out."""
    known = [
        (other, target, [(other.name, printed_areas[id(other)]), *other.merged_from])
        for other, target in zip(read, targets, strict=True)
    ] + [(terrace, None, [(terrace.name, terrace.area)]) for terrace in terraces]
    known = [
        (other, target, [(name, area) for name, area in parts if area is not None])
        for other, target, parts in known
    ]
    sides: dict[str, list[KlRoomRef]] = {}
    for side in NEIGHBOR_SIDES:
        refs: list[KlRoomRef] = []
        for name, area in room.neighbors.get(side, []):
            hits = [
                entry
                for entry in known
                if any(abs(part_area - area) < 0.005 for _, part_area in entry[2])
            ]
            if len(hits) > 1:
                hits = [
                    entry
                    for entry in hits
                    if any(
                        part_name == name and abs(part_area - area) < 0.005
                        for part_name, part_area in entry[2]
                    )
                ] or hits
            if len(hits) != 1 or hits[0][0] is room:
                continue
            other, target, _ = hits[0]
            ref = KlRoomRef(
                name=other.name, area=other.area, room_id=target.room_id if target else None
            )
            if ref not in refs:
                refs.append(ref)
        sides[side] = refs
    return KlRoomNeighbors(**sides)


def _rooms(
    raw_rooms: Any, doubts: list[str], sheet_total: float | None
) -> tuple[list[KlRoom], list[ReadRoom], bool, list[KlProblem]]:
    """Rooms of the flat mapped to system rooms, terraces kept apart (they count in the
    sheet total but are not rooms), whether the kitchen is open to the living room, and the
    areas that are not backed by the sheet."""
    read = []
    quotes: dict[int, str | None] = {}
    for raw in raw_rooms if isinstance(raw_rooms, list) else []:
        name = russian_name(str(raw.get("name") or "").strip()) if isinstance(raw, dict) else ""
        if not name:
            doubts.append(f"запись комнаты без названия: {raw}")
            continue
        area = _as_float(raw.get("area"))
        financial = _as_float(raw.get("financialArea", raw.get("financial_area")))
        kind = room_type(raw.get("type"))
        if kind is None:
            doubts.append(f"тип «{raw.get('type')}» у «{name}» не из списка")
            kind = RoomType.OTHER
        room = ReadRoom(
            name=name,
            area=area,
            financial_area=(financial or area) if area is not None else None,
            kind=kind,
            open_to=_open_to(raw.get("openTo")),
            neighbors=_raw_neighbors(raw.get("neighbors")),
        )
        quote = raw.get("areaText", raw.get("area_text"))
        quotes[id(room)] = str(quote).strip() if quote not in (None, "") else None
        read.append(room)
    # Terraces are not rooms of a planirovka (their area is the flat's, from 1C): no area problems for them;
    # one printed without an area is only noted, it never becomes a room.
    for room in read:
        if is_terrace(room.name) and room.area is None:
            doubts.append(f"у «{room.name}» на листе нет площади")
    read = [room for room in read if not (is_terrace(room.name) and room.area is None)]
    problems = _quote_problems(
        [room for room in read if not is_terrace(room.name)], quotes, sheet_total
    )

    # Neighbours point at the areas the agent read, which the rules below may change.
    printed_areas = {id(room): room.area for room in read}
    for room in read:
        if (
            room.kind is RoomType.BALCONY
            and not is_terrace(room.name)
            and room.area is not None
            and room.area == room.financial_area
        ):
            full = full_balcony_area(room.name, room.area)
            if full is None:
                room.kind = RoomType.OTHER
                doubts.append(
                    f"у «{room.name}» на листе одна площадь {room.area} — полная неизвестна"
                )
            else:
                doubts.append(
                    f"у «{room.name}» на листе одна площадь {room.area}: полная {full} = {room.area} / 0,3"
                )
                room.area = full
    terraces = [room for room in read if is_terrace(room.name)]
    read = merge_open_storage([room for room in read if room not in terraces], doubts)
    note_kitchen_without_living_room(read, doubts)
    note_guessed_studio(read, doubts)
    studio = is_kitchen_studio(read)
    targets = system_rooms([room.kind for room in read], [room.area or 0 for room in read])
    return (
        [
            KlRoom(
                name=room.name,
                area=room.area,
                financial_area=room.financial_area,
                area_text=quotes.get(id(room)) if not room.merged_from else None,
                room_type=room.kind,
                room_id=target.room_id if target else None,
                system_name=target.name if target else None,
                merged_from=[KlRoomPart(name=name, area=area) for name, area in room.merged_from],
                neighbors=_neighbors(room, printed_areas, read, targets, terraces),
            )
            for room, target in zip(read, targets, strict=True)
        ],
        terraces,
        studio,
        problems,
    )


def _unmapped_reason(rooms: list[KlRoom]) -> str | None:
    unknown = [room.name for room in rooms if room.room_type == RoomType.OTHER]
    if unknown:
        return "не определён тип или полная площадь помещения: " + ", ".join(unknown)
    overflow = [room.name for room in rooms if room.room_id is None]
    if overflow:
        return "в справочнике комнат нет места для: " + ", ".join(overflow)
    return None


def _is_unnamed(name: str) -> bool:
    return name.lower().startswith("не определ")


def _sums_match(rooms_sum: float, total: float) -> bool:
    # Rounded: 46.61 - 46.58 is 0.030000000000001 in floats and must still pass.
    return round(abs(rooms_sum - total), 2) <= SUM_TOLERANCE


def build_result(
    answer: dict[str, Any], bi_area: float | None, meta: KlRecognitionMeta
) -> KlRecognitionResult:
    doubts = [str(item) for item in answer.get("doubts") or []]
    checks_in = answer.get("checks") if isinstance(answer.get("checks"), dict) else {}
    sheet_total = _as_float(
        answer.get("sheetTotal", checks_in.get("sheetTotal", answer.get("totalArea")))
    )
    total_text = answer.get("sheetTotalText")
    if (
        sheet_total is not None
        and total_text
        and not _in_quote(sheet_total, quote_numbers(str(total_text)))
    ):
        doubts.append(f"общая площадь {sheet_total}, а на листе напечатано «{total_text}»")
    if answer.get("freeLayout") is True:
        # Free layouts are not recognised at all: the agent stops before reading the rooms.
        return KlRecognitionResult(
            status=RecognitionStatus.FREE_LAYOUT,
            reason=f"свободная планировка: {answer.get('freeLayoutReason') or 'спальня за пунктиром'}",
            rooms=[],
            total_area=sheet_total,
            total_rooms=None,
            checks=KlChecks(sheet_total=sheet_total, bi_area=bi_area),
            doubts=doubts,
            meta=meta,
        )

    rooms, terraces, studio, problems = _rooms(answer.get("rooms"), doubts, sheet_total)

    rooms_sum = round(
        sum(room.financial_area or 0 for room in rooms)
        + sum(t.financial_area or 0 for t in terraces),
        2,
    )
    sheet_ok = None if sheet_total is None else _sums_match(rooms_sum, sheet_total)
    bi_ok = None if bi_area is None else _sums_match(rooms_sum, bi_area)
    # A zone with neither a label nor an area (the sleeping corner of a one-room flat, SHABYT 1, кв. 278)
    # is nothing to enter when the sum already matches the sheet and BI: a note, not a room.
    if sheet_ok is not False and bi_ok is not False and (sheet_ok or bi_ok):
        empty = [room for room in rooms if _is_unnamed(room.name) and room.area is None]
        if empty:
            doubts.append(
                f"зона без подписи и без площади не записана ({len(empty)}): "
                f"сумма комнат сходится без неё ({rooms_sum})"
            )
        rooms = [room for room in rooms if room not in empty]
        problems = [
            problem
            for problem in problems
            if not (
                problem.code is KlProblemCode.AREA_NOT_PRINTED
                and _is_unnamed(problem.room or "")
                and empty
            )
        ]
    unnamed = any(_is_unnamed(room.name) for room in rooms)
    unmapped = _unmapped_reason(rooms)

    if not rooms:
        status, reason = (
            RecognitionStatus.NOT_RECOGNIZED,
            str(answer.get("reason") or "помещения не найдены"),
        )
    elif sheet_ok is None and bi_ok is None:
        status = RecognitionStatus.NOT_RECOGNIZED
        reason = f"сумма комнат {rooms_sum}: не с чем сверить"
    elif (
        sheet_ok is not False
        and bi_ok is not False
        and not unnamed
        and not unmapped
        and not problems
    ):
        status = RecognitionStatus.RECOGNIZED
        reason = f"сумма комнат {rooms_sum} = лист {sheet_total} = BI {bi_area}"
    else:
        status = RecognitionStatus.NOT_RECOGNIZED
        # The cause first: the sums alone read as «the sum did not match» even when it did.
        causes = [problem.text for problem in problems]
        if unnamed:
            causes.append("есть помещение без названия")
        if unmapped:
            causes.append(unmapped)
        reason = "; ".join([*causes, f"сумма комнат {rooms_sum}, лист {sheet_total}, BI {bi_area}"])

    total_rooms = answer.get("totalRooms")
    return KlRecognitionResult(
        status=status,
        reason=reason,
        # Unrecognised sheets carry what was read too: the operator checks it against the sheet
        # instead of typing every room. The status, sums and doubts say it is not a result.
        rooms=rooms,
        is_kitchen_studio=studio if status is RecognitionStatus.RECOGNIZED else False,
        terraces=[KlRoomPart(name=t.name, area=t.area) for t in terraces]
        if status is RecognitionStatus.RECOGNIZED
        else [],
        total_area=sheet_total if sheet_total is not None else rooms_sum,
        total_rooms=total_rooms if isinstance(total_rooms, int) else None,
        checks=KlChecks(
            rooms_sum=rooms_sum,
            sheet_total=sheet_total,
            sheet_ok=sheet_ok,
            bi_area=bi_area,
            bi_ok=bi_ok,
        ),
        doubts=doubts,
        problems=problems,
        meta=meta,
    )
