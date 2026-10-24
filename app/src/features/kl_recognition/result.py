"""Turns Composer's answer into the API result. The sums and the status are recomputed here:
the agent's own arithmetic and verdict are never trusted."""

import json
import re
from typing import Any

from .room_mapping import (
    ReadRoom,
    RoomType,
    full_balcony_area,
    is_terrace,
    merge_open_storage,
    room_type,
    system_rooms,
)
from .schemas import (
    KlChecks,
    KlRecognitionMeta,
    KlRecognitionResult,
    KlRoom,
    KlRoomPart,
    RecognitionStatus,
)

# Sheet areas are rounded to hundredths; correctly read sheets differ from the total by 0–0.03.
SUM_TOLERANCE = 0.03


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


def _rooms(raw_rooms: Any, doubts: list[str]) -> tuple[list[KlRoom], list[ReadRoom]]:
    """Rooms of the flat mapped to system rooms, and terraces kept apart (they count in the
    sheet total but are not rooms)."""
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
            )
        )

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
    targets = system_rooms([room.kind for room in read], [room.area for room in read])
    return [
        KlRoom(
            name=room.name,
            area=room.area,
            financial_area=room.financial_area,
            room_type=room.kind,
            room_id=target.room_id if target else None,
            system_name=target.name if target else None,
            merged_from=[KlRoomPart(name=name, area=area) for name, area in room.merged_from],
        )
        for room, target in zip(read, targets, strict=True)
    ], terraces


def _unmapped_reason(rooms: list[KlRoom]) -> str | None:
    unknown = [room.name for room in rooms if room.room_type == RoomType.OTHER]
    if unknown:
        return "не определён тип или полная площадь помещения: " + ", ".join(unknown)
    overflow = [room.name for room in rooms if room.room_id is None]
    if overflow:
        return "в справочнике комнат нет места для: " + ", ".join(overflow)
    return None


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

    rooms, terraces = _rooms(answer.get("rooms"), doubts)

    rooms_sum = round(
        sum(room.financial_area for room in rooms) + sum(t.financial_area for t in terraces), 2
    )
    sheet_ok = None if sheet_total is None else abs(rooms_sum - sheet_total) <= SUM_TOLERANCE
    bi_ok = None if bi_area is None else abs(rooms_sum - bi_area) <= SUM_TOLERANCE
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
        # Unrecognised sheets carry no rooms: the planirovka is entered by hand, and half-read
        # rooms must not be mistaken for a result. Sums and doubts still explain what failed.
        rooms=rooms if status is RecognitionStatus.RECOGNIZED else [],
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
