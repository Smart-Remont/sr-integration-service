"""Composer client: the SDK gets the model list from the environment, and a call refused by
Cursor's rate limit is retried instead of failing the sheet."""

import json
from typing import Any

import pytest

from src.config import KlRecognitionConfig
from src.features.kl_recognition import composer as composer_module
from src.features.kl_recognition.composer import ComposerClient, ComposerError

FINISHED = json.dumps({"status": "finished", "result": "{}", "usage": {"inputTokens": 1}}).encode()
LIMITED = json.dumps(
    {"status": "error", "error": "You have exceeded the rate limit of 30 requests per minute"}
).encode()


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> ComposerClient:
    monkeypatch.setattr(composer_module, "RATE_LIMIT_WAITS", (0, 0))
    config = KlRecognitionConfig()
    monkeypatch.setattr(config, "cursor_api_key", "key")
    monkeypatch.setattr(config, "cursor_model", "composer-2.5")
    return ComposerClient(config)


async def test_rate_limited_call_is_retried(
    client: ComposerClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    replies = [LIMITED, LIMITED, FINISHED]

    async def run_helper(_task: bytes) -> bytes:
        return replies.pop(0)

    monkeypatch.setattr(client, "_run_helper", run_helper)
    answer = await client.read_plan("prompt", [b"jpeg"])
    assert answer.text == "{}"
    assert replies == []


async def test_rate_limit_that_does_not_lift_fails_the_sheet(
    client: ComposerClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run_helper(_task: bytes) -> bytes:
        return LIMITED

    monkeypatch.setattr(client, "_run_helper", run_helper)
    with pytest.raises(ComposerError, match="rate limit"):
        await client.read_plan("prompt", [b"jpeg"])


async def test_other_errors_are_not_retried(
    client: ComposerClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    async def run_helper(_task: bytes) -> bytes:
        nonlocal calls
        calls += 1
        return json.dumps({"status": "error", "error": "invalid model"}).encode()

    monkeypatch.setattr(client, "_run_helper", run_helper)
    with pytest.raises(ComposerError):
        await client.read_plan("prompt", [b"jpeg"])
    assert calls == 1


async def test_sdk_gets_the_model_list_from_the_environment(
    client: ComposerClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    class Process:
        returncode = 0

        async def communicate(self, _task: bytes) -> tuple[bytes, bytes]:
            return FINISHED, b""

    async def create_subprocess_exec(*_args: Any, env: dict[str, str], **_kwargs: Any) -> Process:
        seen.update(env)
        return Process()

    monkeypatch.setattr(composer_module.asyncio, "create_subprocess_exec", create_subprocess_exec)
    await client.read_plan("prompt", [b"jpeg"])
    assert json.loads(seen["CURSOR_SDK_LOCAL_MODEL_CATALOG_JSON"]) == [{"id": "composer-2.5"}]
