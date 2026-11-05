"""Turns Composer's answer into the API result. The sums and the status are recomputed here:
the agent's own arithmetic and verdict are never trusted."""

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
    system_rooms,
)
from .schemas import (
    KlChecks,
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


def _rooms(raw_rooms: Any, doubts: list[str]) -> tuple[list[KlRoom], list[ReadRoom], bool]:
    """Rooms of the flat mapped to system rooms, terraces kept apart (they count in the
    sheet total but are not rooms), and whether the kitchen is open to the living room."""
    read = []
    for raw in raw_rooms if isinstance(raw_rooms, list) else []:
        area = _as_float(raw.get("area")) if isinstance(raw, dict) else None
        if area is None or not str(raw.get("name") or "").strip():
            doubts.append(f"запись комнаты без названия или площади: {raw}")
            continue
        financial = _as_float(raw.get("financialArea", raw.get("financial_area")))
        kind = room_type(raw.get("type"))
        if kind is None:
            doubts.append(f"тип «{raw.get('type')}» у «{raw['name']}» не из списка")
            kind = RoomType.OTHER
        read.append(
            ReadRoom(
                name=str(raw["name"]).strip(),
                area=area,
                financial_area=financial or area,
                kind=kind,
                open_to=_open_to(raw.get("openTo")),
                neighbors=_raw_neighbors(raw.get("neighbors")),
            )
        )

    # Neighbours point at the areas the agent read, which the rules below may change.
    printed_areas = {id(room): room.area for room in read}
    for room in read:
        if (
            room.kind is RoomType.BALCONY
            and not is_terrace(room.name)
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
    read = merge_open_storage([room for room in read if not is_terrace(room.name)], doubts)
    note_kitchen_without_living_room(read, doubts)
    note_guessed_studio(read, doubts)
    studio = is_kitchen_studio(read)
    targets = system_rooms([room.kind for room in read], [room.area for room in read])
    return (
        [
            KlRoom(
                name=room.name,
                area=room.area,
                financial_area=room.financial_area,
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
    )


def _unmapped_reason(rooms: list[KlRoom]) -> str | None:
    unknown = [room.name for room in rooms if room.room_type == RoomType.OTHER]
    if unknown:
        return "не определён тип или полная площадь помещения: " + ", ".join(unknown)
    overflow = [room.name for room in rooms if room.room_id is None]
    if overflow:
        return "в справочнике комнат нет места для: " + ", ".join(overflow)
    return None


def _sums_match(rooms_sum: float, total: float) -> bool:
    # Rounded: 46.61 - 46.58 is 0.030000000000001 in floats and must still pass.
    return round(abs(rooms_sum - total), 2) <= SUM_TOLERANCE


def build_result(
    answer: dict[str, Any], bi_area: float | None, meta: KlRecognitionMeta
) -> KlRecognitionResult:
    doubts = [str(item) for item in answer.get("doubts") or []]
    checks_in = answer.get("checks") if isinstance(answer.get("checks"), dict) else {}
    sheet_total = _as_float(checks_in.get("sheetTotal", answer.get("totalArea")))
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

    rooms, terraces, studio = _rooms(answer.get("rooms"), doubts)

    rooms_sum = round(
        sum(room.financial_area for room in rooms) + sum(t.financial_area for t in terraces), 2
    )
    sheet_ok = None if sheet_total is None else _sums_match(rooms_sum, sheet_total)
    bi_ok = None if bi_area is None else _sums_match(rooms_sum, bi_area)
    unnamed = any(room.name.lower().startswith("не определ") for room in rooms)
    unmapped = _unmapped_reason(rooms)

    if not rooms:
        status, reason = (
            RecognitionStatus.NOT_RECOGNIZED,
            str(answer.get("reason") or "помещения не найдены"),
        )
    elif sheet_ok is None and bi_ok is None:
        status = RecognitionStatus.NOT_RECOGNIZED
        reason = f"сумма комнат {rooms_sum}: не с чем сверить"
    elif sheet_ok is not False and bi_ok is not False and not unnamed and not unmapped:
        status = RecognitionStatus.RECOGNIZED
        reason = f"сумма комнат {rooms_sum} = лист {sheet_total} = BI {bi_area}"
    else:
        status = RecognitionStatus.NOT_RECOGNIZED
        reason = f"сумма комнат {rooms_sum}, лист {sheet_total}, BI {bi_area}"
        if unnamed:
            reason += "; есть помещение без названия"
        if unmapped:
            reason += f"; {unmapped}"

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
        meta=meta,
    )
