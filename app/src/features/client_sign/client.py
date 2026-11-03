"""Plain document downloads of the signing routes. MyNCA itself is ``src/shared/mynca_client``."""

import httpx
from loguru import logger

from src.php_compat.remote_file import file_get_contents

# PHP curl waited without a limit, and a document is built while the caller waits.
_DOCUMENT_TIMEOUT = httpx.Timeout(timeout=60.0, connect=10.0)


class DocumentFetcher:
    """Plain downloads of documents, without the credentials of MyNCA."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    async def file_get_contents(self, url: str) -> bytes | None:
        return await file_get_contents(url, transport=self._transport)

    async def partner_signed_file(self, url: str) -> bytes:
        """``getSignedFile``: any answer of the partner becomes the file, a failure an empty one."""
        try:
            async with httpx.AsyncClient(
                timeout=_DOCUMENT_TIMEOUT,
                follow_redirects=False,
                transport=self._transport,
            ) as client:
                response = await client.get(url, headers={"Content-Type": "application/json"})
        except (httpx.RequestError, httpx.InvalidURL) as exc:
            logger.warning("Signed file fetch failed | error={error}", error=type(exc).__name__)
            return b""
        logger.info("Signed file fetch | status={status}", status=response.status_code)
        return response.content
