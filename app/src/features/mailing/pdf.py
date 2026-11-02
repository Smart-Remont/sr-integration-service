"""PDF from HTML through ``wkhtmltopdf``, the program PHP calls through ``mikehaertl/phpwkhtmltopdf``."""

import asyncio
import tempfile
from pathlib import Path

_PROGRAM = "wkhtmltopdf"
# The tables have hundreds of lines at most. A stuck program must not hold the cron.
_SECONDS = 120.0


class PdfError(Exception):
    pass


async def html_to_pdf(html: str) -> bytes:
    with tempfile.TemporaryDirectory(prefix="tmp_wkhtmlto_pdf_") as folder:
        source = Path(folder) / "page.html"
        target = Path(folder) / "page.pdf"
        source.write_text(html, encoding="utf-8", errors="surrogateescape")
        await _run(source, target)
        return target.read_bytes()


async def _run(source: Path, target: Path) -> None:
    try:
        process = await asyncio.create_subprocess_exec(
            _PROGRAM,
            str(source),
            str(target),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError as exc:
        raise PdfError(f"{_PROGRAM} did not start: {type(exc).__name__}") from exc
    try:
        code = await asyncio.wait_for(process.wait(), _SECONDS)
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        raise PdfError(f"{_PROGRAM} did not finish in time") from exc
    if code != 0:
        raise PdfError(f"{_PROGRAM} exited with {code}")
