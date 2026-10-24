"""POST /api/v1/kl_recognition/recognize: auth, request checks and how Composer's answer
becomes the result. BI and Composer are replaced by mocks; the status is always recomputed
from the sums, never taken from the agent."""

import asyncio
import base64
import json
import threading
import time
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from src.config import kl_recognition_auth_config
from src.features.kl_recognition.client import KlSheetNotFoundError
from src.features.kl_recognition.composer import ComposerAnswer, ComposerError
from src.features.kl_recognition.deps import get_kl_recognition_service
from src.features.kl_recognition.schemas import KlRecognizeRequest
from src.features.kl_recognition.service import KlRecognitionService
from src.main import app

URL = "/api/v1/kl_recognition/recognize"
FLAT_GUID = "141754c1-b8cb-4f1d-8e4c-bc02a9d6c37d"
PASSWORD = "kl-secret"

ROOMS = [
    {"name": "Кухня", "type": "Кухня", "area": 11.19, "financialArea": 11.19},
    {"name": "Лоджия", "type": "Балкон", "area": 3.08, "financialArea": 1.54},
    {"name": "Гостиная", "type": "Гостиная", "area": 20.85, "financialArea": 20.85},
]
ROOMS_SUM = 33.58


def composer_answer(**overrides: Any) -> ComposerAnswer:
    body = {
        "status": "recognized",
        "rooms": ROOMS,
        "totalArea": ROOMS_SUM,
        "totalRooms": 1,
        "checks": {"sheetTotal": ROOMS_SUM},
        "doubts": [],
    } | overrides
    text = "Сверил подписи.\n```json\n" + json.dumps(body, ensure_ascii=False) + "\n```"
    return ComposerAnswer(text=text, seconds=17, input_tokens=17000, output_tokens=1500)


@pytest.fixture(autouse=True)
def credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kl_recognition_auth_config, "username", "kl")
    monkeypatch.setattr(kl_recognition_auth_config, "password", PASSWORD)


@pytest.fixture
def sheet_client() -> AsyncMock:
    client = AsyncMock()
    client.download.return_value = b"%PDF-1.7"
    return client


@pytest.fixture
def composer() -> AsyncMock:
    composer = AsyncMock()
    composer.read_plan.return_value = composer_answer()
    return composer


@pytest.fixture
async def client(sheet_client: AsyncMock, composer: AsyncMock) -> AsyncIterator[AsyncClient]:
    service = KlRecognitionService(
        sheet_client=sheet_client,
        composer=composer,
        build_images=lambda _pdf: [b"jpeg"] * 5,
        model="composer-2.5",
        prompt_template="Площадь BI: `{BI_AREA}`. Картинки: {IMAGES}",
        build_page_images=lambda _pdf: [b"page"] * 5,
    )
    app.dependency_overrides[get_kl_recognition_service] = lambda: service
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
    app.dependency_overrides.clear()


def auth(password: str | None = None) -> dict[str, str]:
    token = base64.b64encode(f"kl:{password or PASSWORD}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


async def recognize(client: AsyncClient, body: dict[str, Any] | None = None) -> dict[str, Any]:
    response = await client.post(
        URL, json=body or {"flat_guid": FLAT_GUID, "bi_area": ROOMS_SUM}, headers=auth()
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_requires_basic_auth(client: AsyncClient) -> None:
    assert (await client.post(URL, json={"flat_guid": FLAT_GUID})).status_code == 401
    wrong = await client.post(URL, json={"flat_guid": FLAT_GUID}, headers=auth("wrong"))
    assert wrong.status_code == 401


async def test_not_configured_credentials_mean_503(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(kl_recognition_auth_config, "password", "")
    response = await client.post(URL, json={"flat_guid": FLAT_GUID}, headers=auth())
    assert response.status_code == 503


@pytest.mark.parametrize("body", [{}, {"flat_guid": FLAT_GUID, "pdf_url": "https://a.kz/kl.pdf"}])
async def test_needs_exactly_one_source(client: AsyncClient, body: dict[str, Any]) -> None:
    assert (await client.post(URL, json=body, headers=auth())).status_code == 422


async def test_recognized_when_sum_matches_sheet_and_bi(
    client: AsyncClient, composer: AsyncMock
) -> None:
    result = await recognize(client)

    assert result["status"] == "recognized"
    assert result["rooms"][1] == {
        "name": "Лоджия",
        "area": 3.08,
        "financial_area": 1.54,
        "room_type": "Балкон",
        "room_id": 20,
        "system_name": "Балкон",
        "merged_from": [],
    }
    assert result["checks"] == {
        "rooms_sum": ROOMS_SUM,
        "sheet_total": ROOMS_SUM,
        "sheet_ok": True,
        "bi_area": ROOMS_SUM,
        "bi_ok": True,
    }
    assert result["total_rooms"] == 1
    assert result["meta"]["seconds"] == 17
    prompt, images = composer.read_plan.await_args.args
    assert prompt.startswith(f"Площадь BI: `{ROOMS_SUM:.2f}`. Картинки: к сообщению приложены")
    assert "вырезанный со стр. 2" in prompt
    assert len(images) == 5


async def test_not_recognized_when_sum_differs_from_bi(client: AsyncClient) -> None:
    result = await recognize(client, {"flat_guid": FLAT_GUID, "bi_area": 35.0})
    assert result["status"] == "not_recognized"
    assert result["checks"]["bi_ok"] is False
    # half-read rooms are not handed over: the planirovka is entered by hand
    assert result["rooms"] == []


async def test_agent_verdict_is_not_trusted(client: AsyncClient, composer: AsyncMock) -> None:
    # The agent claims "auto" with a sheet total its own rooms do not add up to.
    composer.read_plan.return_value = composer_answer(checks={"sheetTotal": 40.0})
    result = await recognize(client)
    assert result["status"] == "not_recognized"
    assert result["checks"]["sheet_ok"] is False


async def test_unnamed_room_is_not_recognized(client: AsyncClient, composer: AsyncMock) -> None:
    rooms = [*ROOMS[:2], {"name": "Не определено", "area": 20.85, "financialArea": 20.85}]
    composer.read_plan.return_value = composer_answer(rooms=rooms)
    result = await recognize(client)
    assert result["status"] == "not_recognized"
    assert "без названия" in result["reason"]


async def test_room_of_unknown_type_is_not_recognized(
    client: AsyncClient, composer: AsyncMock
) -> None:
    # The sums match, but «Сауна» has no room in the system.
    rooms = [*ROOMS[:2], {"name": "Сауна", "type": "Другое", "area": 20.85, "financialArea": 20.85}]
    composer.read_plan.return_value = composer_answer(rooms=rooms)
    result = await recognize(client)
    assert result["status"] == "not_recognized"
    assert "не определён тип или полная площадь помещения: Сауна" in result["reason"]
    assert result["rooms"] == []


async def test_free_layout_is_not_recognised(client: AsyncClient, composer: AsyncMock) -> None:
    answer = {"freeLayout": True, "freeLayoutReason": "спальня за пунктиром", "rooms": []}
    composer.read_plan.return_value = ComposerAnswer(
        text=json.dumps(answer, ensure_ascii=False), seconds=9, input_tokens=17000, output_tokens=60
    )
    result = await recognize(client)
    assert result["status"] == "free_layout"
    assert result["reason"] == "свободная планировка: спальня за пунктиром"
    assert result["rooms"] == []


async def test_storage_behind_a_dashed_line_is_merged_without_changing_the_sum(
    client: AsyncClient, composer: AsyncMock
) -> None:
    pantry = {
        "name": "Кладовая",
        "type": "Кладовая",
        "area": 3.16,
        "financialArea": 3.16,
        "openTo": {"name": "Кухня", "area": 11.19},
    }
    composer.read_plan.return_value = composer_answer(
        rooms=[*ROOMS, pantry], checks={"sheetTotal": ROOMS_SUM + 3.16}
    )
    result = await recognize(client, {"flat_guid": FLAT_GUID, "bi_area": ROOMS_SUM + 3.16})
    assert result["status"] == "recognized"
    kitchen = result["rooms"][0]
    assert (kitchen["area"], kitchen["system_name"]) == (14.35, "Кухня")
    assert [part["name"] for part in kitchen["merged_from"]] == ["Кухня", "Кладовая"]
    assert len(result["rooms"]) == 3


WARDROBE_ROOMS = [
    *ROOMS,
    {"name": "Спальня", "type": "Спальня", "area": 14.21, "financialArea": 14.21},
    {"name": "Гард.", "type": "Гардеробная", "area": 2.11, "financialArea": 2.11, "openTo": None},
]
WARDROBE_SUM = round(ROOMS_SUM + 14.21 + 2.11, 2)


def recheck_answer(open_to: dict | None) -> ComposerAnswer:
    body = {"storages": [{"name": "Гард.", "area": 2.11, "openTo": open_to}]}
    return ComposerAnswer(
        text=json.dumps(body, ensure_ascii=False), seconds=8, input_tokens=17500, output_tokens=90
    )


async def test_wardrobe_missed_in_the_first_answer_is_asked_again_and_merged(
    client: AsyncClient, composer: AsyncMock
) -> None:
    composer.read_plan.side_effect = [
        composer_answer(rooms=WARDROBE_ROOMS, checks={"sheetTotal": WARDROBE_SUM}),
        recheck_answer({"name": "Спальня", "area": 14.21}),
    ]
    result = await recognize(client, {"flat_guid": FLAT_GUID, "bi_area": WARDROBE_SUM})

    assert composer.read_plan.await_count == 2
    second_prompt = composer.read_plan.await_args_list[1].args[0]
    assert "- Гард. 2.11" in second_prompt
    assert result["status"] == "recognized"
    bedroom = next(r for r in result["rooms"] if r["room_type"] == "Спальня")
    assert bedroom["area"] == 16.32
    assert [p["name"] for p in bedroom["merged_from"]] == ["Спальня", "Гард."]
    assert result["meta"]["seconds"] == 17 + 8


async def test_wardrobe_stays_separate_when_the_second_look_finds_a_wall(
    client: AsyncClient, composer: AsyncMock
) -> None:
    composer.read_plan.side_effect = [
        composer_answer(rooms=WARDROBE_ROOMS, checks={"sheetTotal": WARDROBE_SUM}),
        recheck_answer(None),
    ]
    result = await recognize(client, {"flat_guid": FLAT_GUID, "bi_area": WARDROBE_SUM})
    assert result["status"] == "recognized"
    assert any(r["system_name"] == "Гардеробная" and r["area"] == 2.11 for r in result["rooms"])


async def test_no_second_look_when_every_wardrobe_is_tied_to_a_room(
    client: AsyncClient, composer: AsyncMock
) -> None:
    rooms = [
        *WARDROBE_ROOMS[:-1],
        {**WARDROBE_ROOMS[-1], "openTo": {"name": "Спальня", "area": 14.21}},
    ]
    composer.read_plan.return_value = composer_answer(
        rooms=rooms, checks={"sheetTotal": WARDROBE_SUM}
    )
    await recognize(client, {"flat_guid": FLAT_GUID, "bi_area": WARDROBE_SUM})
    assert composer.read_plan.await_count == 1


async def test_whole_page_is_read_when_the_cut_plan_misses_rooms(
    client: AsyncClient, composer: AsyncMock
) -> None:
    # The cut held only part of the plan: its rooms are far below the sheet total.
    composer.read_plan.side_effect = [
        composer_answer(rooms=ROOMS[:1], checks={"sheetTotal": ROOMS_SUM}),
        composer_answer(),
    ]
    result = await recognize(client)

    assert result["status"] == "recognized"
    first, second = (call.args for call in composer.read_plan.await_args_list)
    assert first[1] == [b"jpeg"] * 5 and "вырезанный со стр. 2" in first[0]
    assert second[1] == [b"page"] * 5 and "стр. 2 квартирного листа целиком" in second[0]
    assert result["meta"]["seconds"] == 17 * 2


async def test_whole_page_is_read_when_no_plan_is_found_on_the_cut(
    client: AsyncClient, composer: AsyncMock
) -> None:
    composer.read_plan.side_effect = [
        composer_answer(rooms=[], reason="нет плана на стр. 2", checks={}, totalArea=None),
        composer_answer(),
    ]
    result = await recognize(client)
    assert result["status"] == "recognized"
    assert composer.read_plan.await_count == 2


async def test_small_misprint_on_the_sheet_does_not_trigger_the_whole_page(
    client: AsyncClient, composer: AsyncMock
) -> None:
    # 0.10 off the sheet total is a misprint on the sheet, not a bad cut.
    composer.read_plan.return_value = composer_answer(checks={"sheetTotal": ROOMS_SUM + 0.1})
    result = await recognize(client)
    assert result["status"] == "not_recognized"
    assert composer.read_plan.await_count == 1


async def test_whole_page_failure_keeps_both_reasons(
    client: AsyncClient, composer: AsyncMock
) -> None:
    composer.read_plan.return_value = composer_answer(
        rooms=ROOMS[:1], checks={"sheetTotal": ROOMS_SUM}
    )
    result = await recognize(client)
    assert result["status"] == "not_recognized"
    assert "по вырезке плана: сумма комнат" in result["reason"]
    assert composer.read_plan.await_count == 2


async def test_terrace_counts_in_the_sum_but_is_not_a_room(
    client: AsyncClient, composer: AsyncMock
) -> None:
    # Darmen 2: a terrace 5,19 (1,56) and a loggia 2,84 (1,42) — operators enter only the loggia.
    terrace = {"name": "Терраса", "type": "Балкон", "area": 5.19, "financialArea": 1.56}
    total = round(ROOMS_SUM + 1.56, 2)
    composer.read_plan.return_value = composer_answer(
        rooms=[*ROOMS, terrace], checks={"sheetTotal": total}
    )
    result = await recognize(client, {"flat_guid": FLAT_GUID, "bi_area": total})

    assert result["status"] == "recognized"
    assert result["checks"]["rooms_sum"] == total
    assert [r["name"] for r in result["rooms"]] == ["Кухня", "Лоджия", "Гостиная"]
    assert next(r for r in result["rooms"] if r["name"] == "Лоджия")["system_name"] == "Балкон"
    assert result["terraces"] == [{"name": "Терраса", "area": 5.19}]


async def test_balcony_with_one_printed_area_gets_the_full_area(
    client: AsyncClient, composer: AsyncMock
) -> None:
    # Sanat: only the reduced area 0,99 is printed; operators enter 0,99 / 0,3 = 3,30.
    balcony = {"name": "Балкон", "type": "Балкон", "area": 0.99, "financialArea": 0.99}
    total = round(ROOMS[0]["area"] + ROOMS[2]["area"] + 0.99, 2)
    composer.read_plan.return_value = composer_answer(
        rooms=[ROOMS[0], ROOMS[2], balcony], checks={"sheetTotal": total}
    )
    result = await recognize(client, {"flat_guid": FLAT_GUID, "bi_area": total})
    assert result["status"] == "recognized"
    room = next(r for r in result["rooms"] if r["name"] == "Балкон")
    assert (room["area"], room["financial_area"]) == (3.3, 0.99)


async def test_loggia_with_one_printed_area_is_not_recognized(
    client: AsyncClient, composer: AsyncMock
) -> None:
    loggia = {"name": "Лоджия", "type": "Балкон", "area": 1.54, "financialArea": 1.54}
    composer.read_plan.return_value = composer_answer(rooms=[ROOMS[0], loggia, ROOMS[2]])
    result = await recognize(client)
    assert result["status"] == "not_recognized"
    assert "Лоджия" in result["reason"]


async def test_type_outside_the_list_is_reported(client: AsyncClient, composer: AsyncMock) -> None:
    rooms = [*ROOMS[:2], {"name": "Гостиная", "type": "Зал", "area": 20.85, "financialArea": 20.85}]
    composer.read_plan.return_value = composer_answer(rooms=rooms)
    result = await recognize(client)
    assert result["status"] == "not_recognized"
    assert "тип «Зал» у «Гостиная» не из списка" in result["doubts"]


async def test_not_recognized_when_bi_has_no_kl(
    client: AsyncClient, sheet_client: AsyncMock, composer: AsyncMock
) -> None:
    sheet_client.download.side_effect = KlSheetNotFoundError("BI не отдал КЛ (HTTP 500)")
    result = await recognize(client)
    assert result["status"] == "not_recognized"
    assert result["reason"] == "BI не отдал КЛ (HTTP 500)"
    assert result["rooms"] == []
    composer.read_plan.assert_not_awaited()


async def test_not_recognized_when_composer_fails(client: AsyncClient, composer: AsyncMock) -> None:
    composer.read_plan.side_effect = ComposerError("Composer не уложился в 300 с")
    result = await recognize(client)
    assert result["status"] == "not_recognized"
    assert result["reason"] == "Composer не уложился в 300 с"


async def test_not_recognized_when_answer_has_no_json(
    client: AsyncClient, composer: AsyncMock
) -> None:
    composer.read_plan.return_value = ComposerAnswer(
        text="План не виден", seconds=5, input_tokens=None, output_tokens=None
    )
    result = await recognize(client)
    assert result["status"] == "not_recognized"
    assert result["reason"] == "ответ агента без JSON"


async def test_plan_images_are_built_two_at_a_time(composer: AsyncMock) -> None:
    # A burst of requests used to crop every page at once and run out of memory.
    running = peak = 0
    lock = threading.Lock()

    def slow_build(_pdf: bytes) -> list[bytes]:
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        time.sleep(0.05)
        with lock:
            running -= 1
        return [b"jpeg"] * 5

    sheet_client = AsyncMock()
    sheet_client.download.return_value = b"%PDF-1.7"
    service = KlRecognitionService(
        sheet_client=sheet_client,
        composer=composer,
        build_images=slow_build,
        model="composer-2.5",
        prompt_template="{BI_AREA}",
    )
    request = KlRecognizeRequest(flat_guid=FLAT_GUID, bi_area=ROOMS_SUM)
    results = await asyncio.gather(*(service.recognize(request) for _ in range(10)))

    assert all(result.status == "recognized" for result in results)
    assert peak == 2
