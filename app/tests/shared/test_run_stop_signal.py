"""SIGTERM reaches a cron run under the real gunicorn and UvicornWorker, and uvicorn still stops."""

import os
import signal
import socket
import subprocess
import sys
import textwrap
import time
from pathlib import Path

_APP_DIR = Path(__file__).parents[2]
_SERVER = """
    import asyncio
    from collections.abc import AsyncGenerator
    from contextlib import asynccontextmanager
    from pathlib import Path

    from fastapi import FastAPI

    from _gunicorn.app_options import get_app_options
    from _gunicorn.application import GunicornApplication
    from src.shared.cron_run import install_run_stop_signal, run_stop_requested

    report = Path({report!r})


    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        install_run_stop_signal()
        yield


    app = FastAPI(lifespan=lifespan)


    @app.get("/run")
    async def run() -> dict[str, int]:
        report.write_text("started")
        rows = 0
        while rows < 600:
            if run_stop_requested():
                report.write_text("stopped between rows")
                return {{"rows": rows}}
            await asyncio.sleep(0.05)
            rows += 1
        report.write_text("not stopped")
        return {{"rows": rows}}


    GunicornApplication(
        application=app,
        options=get_app_options("127.0.0.1", {port}, 60, 20, 1, "warning"),
    ).run()
"""


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _wait_until(condition, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return False


def _answers(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), 0.2):
            return True
    except OSError:
        return False


def test_sigterm_stops_a_running_cron_between_rows_and_the_worker_exits(tmp_path: Path) -> None:
    port = _free_port()
    report = tmp_path / "report"
    code = textwrap.dedent(_SERVER).format(report=str(report), port=port)
    server = subprocess.Popen(  # noqa: S603 — this interpreter, this test's own text
        [sys.executable, "-c", code],
        cwd=_APP_DIR,
        env={**os.environ, "PYTHONPATH": str(_APP_DIR)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    answer = None
    try:
        assert _wait_until(lambda: _answers(port), 30)
        request = subprocess.Popen(  # noqa: S603 — this interpreter, the address of this test
            [
                sys.executable,
                "-c",
                f"import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:{port}/run').read())",
            ],
            stdout=subprocess.PIPE,
        )
        assert _wait_until(report.exists, 10)

        server.send_signal(signal.SIGTERM)

        answer, _ = request.communicate(timeout=15)
        assert server.wait(timeout=15) == 0
    finally:
        server.kill()
        server.wait()
    assert report.read_text() == "stopped between rows"
    assert b"rows" in (answer or b"")
