"""Turns Composer's answer into the API result. The sums and the status are recomputed here:
the agent's own arithmetic and verdict are never trusted."""

import json
import re
from typing import Any

from .room_mapping import RoomType, room_type, system_rooms
from .schemas import KlChecks, KlRecognitionMeta, KlRecognitionResult, KlRoom, RecognitionStatus

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


def manual_result(
    reason: str, bi_area: float | None, meta: KlRecognitionMeta
) -> KlRecognitionResult:
    return KlRecognitionResult(
        status=RecognitionStatus.MANUAL,
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


def _rooms(raw_rooms: Any, doubts: list[str]) -> list[KlRoom]:
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
        read.append((str(raw["name"]).strip(), area, financial or area, kind))

    targets = system_rooms([kind for *_, kind in read], [area for _, area, *_ in read])
    return [
        KlRoom(
            name=name,
            area=area,
            financial_area=financial,
            room_type=kind,
            room_id=target.room_id if target else None,
            system_name=target.name if target else None,
        )
        for (name, area, financial, kind), target in zip(read, targets, strict=True)
    ]


def _unmapped_reason(rooms: list[KlRoom]) -> str | None:
    unknown = [room.name for room in rooms if room.room_type == RoomType.OTHER]
    if unknown:
        return "тип помещения не определён: " + ", ".join(unknown)
    overflow = [room.name for room in rooms if room.room_id is None]
    if overflow:
        return "в справочнике комнат нет места для: " + ", ".join(overflow)
    return None


def build_result(
    answer: dict[str, Any], bi_area: float | None, meta: KlRecognitionMeta
) -> KlRecognitionResult:
    doubts = [str(item) for item in answer.get("doubts") or []]
    rooms = _rooms(answer.get("rooms"), doubts)
    checks_in = answer.get("checks") if isinstance(answer.get("checks"), dict) else {}
    sheet_total = _as_float(checks_in.get("sheetTotal", answer.get("totalArea")))

    rooms_sum = round(sum(room.financial_area for room in rooms), 2)
    sheet_ok = None if sheet_total is None else abs(rooms_sum - sheet_total) <= SUM_TOLERANCE
    bi_ok = None if bi_area is None else abs(rooms_sum - bi_area) <= SUM_TOLERANCE
    unnamed = any(room.name.lower().startswith("не определ") for room in rooms)
    unmapped = _unmapped_reason(rooms)

    if not rooms:
        status, reason = (
            RecognitionStatus.MANUAL,
            str(answer.get("reason") or "помещения не найдены"),
        )
    elif sheet_ok is None and bi_ok is None:
        status, reason = RecognitionStatus.MANUAL, f"сумма комнат {rooms_sum}: не с чем сверить"
    elif sheet_ok is not False and bi_ok is not False and not unnamed and not unmapped:
        status = RecognitionStatus.AUTO
        reason = f"сумма комнат {rooms_sum} = лист {sheet_total} = BI {bi_area}"
    else:
        status = RecognitionStatus.MANUAL
        reason = f"сумма комнат {rooms_sum}, лист {sheet_total}, BI {bi_area}"
        if unnamed:
            reason += "; есть помещение без названия"
        if unmapped:
            reason += f"; {unmapped}"

    total_rooms = answer.get("totalRooms")
    return KlRecognitionResult(
        status=status,
        reason=reason,
        rooms=rooms,
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
