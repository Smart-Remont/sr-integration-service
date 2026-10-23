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
        "status": "auto",
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
        prompt_template="Площадь BI: `{BI_AREA}`",
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


async def test_auto_when_sum_matches_sheet_and_bi(client: AsyncClient, composer: AsyncMock) -> None:
    result = await recognize(client)

    assert result["status"] == "auto"
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
    assert prompt == f"Площадь BI: `{ROOMS_SUM:.2f}`"
    assert len(images) == 5


async def test_manual_when_sum_differs_from_bi(client: AsyncClient) -> None:
    result = await recognize(client, {"flat_guid": FLAT_GUID, "bi_area": 35.0})
    assert result["status"] == "manual"
    assert result["checks"]["bi_ok"] is False


async def test_agent_verdict_is_not_trusted(client: AsyncClient, composer: AsyncMock) -> None:
    # The agent claims "auto" with a sheet total its own rooms do not add up to.
    composer.read_plan.return_value = composer_answer(checks={"sheetTotal": 40.0})
    result = await recognize(client)
    assert result["status"] == "manual"
    assert result["checks"]["sheet_ok"] is False


async def test_unnamed_room_goes_to_manual(client: AsyncClient, composer: AsyncMock) -> None:
    rooms = [*ROOMS[:2], {"name": "Не определено", "area": 20.85, "financialArea": 20.85}]
    composer.read_plan.return_value = composer_answer(rooms=rooms)
    result = await recognize(client)
    assert result["status"] == "manual"
    assert "без названия" in result["reason"]


async def test_room_of_unknown_type_goes_to_manual(
    client: AsyncClient, composer: AsyncMock
) -> None:
    # The sums match, but «Сауна» has no room in the system.
    rooms = [*ROOMS[:2], {"name": "Сауна", "type": "Другое", "area": 20.85, "financialArea": 20.85}]
    composer.read_plan.return_value = composer_answer(rooms=rooms)
    result = await recognize(client)
    assert result["status"] == "manual"
    assert "тип помещения не определён: Сауна" in result["reason"]
    assert result["rooms"][2]["room_id"] is None


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
    assert result["status"] == "auto"
    kitchen = result["rooms"][0]
    assert (kitchen["area"], kitchen["system_name"]) == (14.35, "Кухня")
    assert [part["name"] for part in kitchen["merged_from"]] == ["Кухня", "Кладовая"]
    assert len(result["rooms"]) == 3


async def test_type_outside_the_list_is_reported(client: AsyncClient, composer: AsyncMock) -> None:
    rooms = [*ROOMS[:2], {"name": "Гостиная", "type": "Зал", "area": 20.85, "financialArea": 20.85}]
    composer.read_plan.return_value = composer_answer(rooms=rooms)
    result = await recognize(client)
    assert result["status"] == "manual"
    assert result["rooms"][2]["room_type"] == "Другое"
    assert "тип «Зал» у «Гостиная» не из списка" in result["doubts"]


async def test_manual_when_bi_has_no_kl(
    client: AsyncClient, sheet_client: AsyncMock, composer: AsyncMock
) -> None:
    sheet_client.download.side_effect = KlSheetNotFoundError("BI не отдал КЛ (HTTP 500)")
    result = await recognize(client)
    assert result["status"] == "manual"
    assert result["reason"] == "BI не отдал КЛ (HTTP 500)"
    assert result["rooms"] == []
    composer.read_plan.assert_not_awaited()


async def test_manual_when_composer_fails(client: AsyncClient, composer: AsyncMock) -> None:
    composer.read_plan.side_effect = ComposerError("Composer не уложился в 300 с")
    result = await recognize(client)
    assert result["status"] == "manual"
    assert result["reason"] == "Composer не уложился в 300 с"


async def test_manual_when_answer_has_no_json(client: AsyncClient, composer: AsyncMock) -> None:
    composer.read_plan.return_value = ComposerAnswer(
        text="План не виден", seconds=5, input_tokens=None, output_tokens=None
    )
    result = await recognize(client)
    assert result["status"] == "manual"
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

    assert all(result.status == "auto" for result in results)
    assert peak == 2
