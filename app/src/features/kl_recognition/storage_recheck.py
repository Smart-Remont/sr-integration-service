"""Second look at storerooms and wardrobes the agent did not tie to a room.

In one pass the agent misses a dashed line next to a wardrobe in about one answer out of
seven (says «no neighbour» or names the corridor), while it practically never claims a dashed
line where a wall or a door is. So a short follow-up question about only those rooms is
asked, and a dashed line found in either answer counts.
"""

from pathlib import Path
from typing import Any

from .room_mapping import MERGE_TARGETS, RoomType, room_type

RECHECK_PROMPT_PATH = Path(__file__).parent / "prompt_recheck.md"


def _area(value: Any) -> float | None:
    try:
        return round(float(value), 2)
    except TypeError, ValueError:
        return None


def _neighbour_type(rooms: list[dict], open_to: Any) -> RoomType | None:
    """Type of the room `open_to` points at, found among the read rooms by area (and name)."""
    if not isinstance(open_to, dict):
        return None
    area = _area(open_to.get("area"))
    if area is None:
        return None
    matches = [r for r in rooms if _area(r.get("area")) == area]
    if len(matches) > 1:
        matches = [r for r in matches if r.get("name") == open_to.get("name")] or matches
    return room_type(matches[0].get("type")) if len(matches) == 1 else None


def _rooms(answer: dict[str, Any]) -> list[dict]:
    rooms = answer.get("rooms")
    return [r for r in rooms if isinstance(r, dict)] if isinstance(rooms, list) else []


def storages_to_recheck(answer: dict[str, Any]) -> list[dict]:
    """Storerooms and wardrobes whose `openTo` does not lead to a room they could be added to."""
    if answer.get("freeLayout") is True:
        return []
    rooms = _rooms(answer)
    result = []
    for room in rooms:
        targets = MERGE_TARGETS.get(room_type(room.get("type")) or RoomType.OTHER)
        if targets and _neighbour_type(rooms, room.get("openTo")) not in targets:
            result.append(room)
    return result


def recheck_prompt(template: str, answer: dict[str, Any], storages: list[dict]) -> str:
    listed = "\n".join(
        f"- {r.get('name')} {r.get('area')} ({r.get('type')})" for r in _rooms(answer)
    )
    asked = "\n".join(f"- {r.get('name')} {r.get('area')}" for r in storages)
    return template.replace("{ROOMS}", listed).replace("{STORAGES}", asked)


def apply_recheck(answer: dict[str, Any], recheck: dict[str, Any] | None) -> int:
    """Copies a dashed-line neighbour from the follow-up answer when it is one the room can be
    added to. Returns how many rooms got one."""
    rooms = _rooms(answer)
    found = 0
    for item in (recheck or {}).get("storages") or []:
        if not isinstance(item, dict):
            continue
        area = _area(item.get("area"))
        for room in rooms:
            targets = MERGE_TARGETS.get(room_type(room.get("type")) or RoomType.OTHER)
            if not targets or _area(room.get("area")) != area:
                continue
            if _neighbour_type(rooms, room.get("openTo")) in targets:
                break
            if _neighbour_type(rooms, item.get("openTo")) in targets:
                room["openTo"] = item["openTo"]
                found += 1
            break
    return found
