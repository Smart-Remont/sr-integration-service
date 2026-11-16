"""The form page: one static file with its style and script inline, read once at import.

The page shows no data of its own: the list of residential complexes and the answer to the form
come from ``/api/bi-badge/*`` of the same address.
"""

import base64
import hashlib
import re
from pathlib import Path

from fastapi.responses import HTMLResponse

_PAGE = (Path(__file__).parent / "page.html").read_bytes()
_SCRIPT_RE = re.compile(rb"<script>(.*?)</script>", re.DOTALL)


def _script_hashes(page: bytes) -> str:
    """CSP sources of the inline scripts: a script injected into the page will not run."""
    hashes = (
        base64.b64encode(hashlib.sha256(script).digest()).decode()
        for script in _SCRIPT_RE.findall(page)
    )
    return " ".join(f"'sha256-{value}'" for value in hashes)


_HEADERS = {
    "Cache-Control": "public, max-age=300",
    "Content-Security-Policy": (
        "default-src 'none'; "
        f"script-src {_script_hashes(_PAGE)}; "
        "style-src 'unsafe-inline'; "
        "connect-src 'self'; "
        "img-src 'self' data:; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


def bi_badge_page_response() -> HTMLResponse:
    return HTMLResponse(content=_PAGE, headers=_HEADERS)
