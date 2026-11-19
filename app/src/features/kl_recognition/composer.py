import asyncio
import base64
import json
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from src.config import KlRecognitionConfig

HELPER = Path(__file__).parent / "composer_sdk" / "recognize.mjs"
# Pauses before retrying a call refused by Cursor's rate limit.
RATE_LIMIT_WAITS = (15, 30, 60)


class ComposerError(Exception):
    """Composer did not answer: no key, Node or SDK failure, timeout."""


@dataclass(frozen=True)
class ComposerAnswer:
    text: str
    seconds: int
    input_tokens: int | None
    output_tokens: int | None


_slots: dict[int, asyncio.Semaphore] = {}


def _parallel_slots(limit: int) -> asyncio.Semaphore:
    """Shared by all requests of the worker: each run holds an SDK agent and a Node process."""
    if limit not in _slots:
        _slots[limit] = asyncio.Semaphore(limit)
    return _slots[limit]


class ComposerClient:
    """Runs Composer (Cursor SDK) on the plan images through the Node helper."""

    def __init__(self, config: KlRecognitionConfig) -> None:
        self._config = config

    async def read_plan(self, prompt: str, images: list[bytes]) -> ComposerAnswer:
        if not self._config.cursor_api_key:
            raise ComposerError("CURSOR_API_KEY не задан")
        task = json.dumps(
            {
                "model": self._config.cursor_model,
                "prompt": prompt,
                "images": [base64.b64encode(image).decode() for image in images],
            }
        ).encode()
        for wait in (*RATE_LIMIT_WAITS, None):
            try:
                return await self._read_once(task)
            except ComposerError as exc:
                # A burst of sheets can still hit Cursor's per-minute limits: wait, outside the
                # parallel slot, and try again instead of failing the sheet.
                if wait is None or "rate limit" not in str(exc).lower():
                    raise
                logger.warning("Composer rate limit, retry in {wait} s", wait=wait)
                await asyncio.sleep(wait)
        raise AssertionError("unreachable")

    async def _read_once(self, task: bytes) -> ComposerAnswer:
        async with _parallel_slots(self._config.max_parallel):
            started = time.monotonic()
            stdout = await self._run_helper(task)
            seconds = round(time.monotonic() - started)

        try:
            reply = json.loads(stdout)
        except ValueError as exc:
            raise ComposerError("помощник SDK вернул не JSON") from exc
        if reply.get("status") != "finished" or not reply.get("result"):
            raise ComposerError(f"Composer не ответил: {reply.get('error') or reply.get('status')}")
        usage = reply.get("usage") or {}
        return ComposerAnswer(
            text=reply["result"],
            seconds=seconds,
            input_tokens=usage.get("inputTokens"),
            output_tokens=usage.get("outputTokens"),
        )

    async def _run_helper(self, task: bytes) -> bytes:
        # The SDK keeps local agent state under HOME; the service user has no home directory.
        with tempfile.TemporaryDirectory(prefix="kl-composer-home-") as home:
            env = {
                "PATH": os.environ.get("PATH", ""),
                "HOME": home,
                "CURSOR_API_KEY": self._config.cursor_api_key,
                # The SDK lists the account's models on every new agent to validate the model id,
                # and that call is limited to 30 a minute: hand it the model instead.
                "CURSOR_SDK_LOCAL_MODEL_CATALOG_JSON": json.dumps(
                    [{"id": self._config.cursor_model}]
                ),
            }
            try:
                process = await asyncio.create_subprocess_exec(
                    self._config.node_bin,
                    str(HELPER),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                )
            except FileNotFoundError as exc:
                raise ComposerError(f"не найден Node.js: {self._config.node_bin}") from exc
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(task), timeout=self._config.agent_timeout_seconds
                )
            except TimeoutError as exc:
                process.kill()
                await process.wait()
                raise ComposerError(
                    f"Composer не уложился в {self._config.agent_timeout_seconds} с"
                ) from exc
        if process.returncode != 0:
            logger.error(
                "Composer helper exit {code}: {err}", code=process.returncode, err=stderr[-1000:]
            )
            # The helper prints a JSON error itself; empty stdout means Node did not even start.
            if not stdout.strip():
                last_line = stderr.decode(errors="replace").strip().splitlines()[-1:] or ["—"]
                raise ComposerError(
                    f"помощник SDK упал (код {process.returncode}): {last_line[0][:200]}"
                )
        return stdout
