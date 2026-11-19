"""``@file_get_contents`` of a remote address."""

import httpx
from loguru import logger

# default_socket_timeout of PHP. Also for documents PHP curl waited for without a limit: they are
# built while the caller waits.
DOCUMENT_TIMEOUT = httpx.Timeout(timeout=60.0, connect=10.0)
# Added by httpx and absent from what PHP curl sends.
HTTPX_HEADERS_PHP_DOES_NOT_SEND = ("User-Agent", "Accept-Encoding", "Connection")
_FIRST_HTTP_ERROR = 400


async def file_get_contents(
    url: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    max_bytes: int | None = None,
) -> bytes | None:
    """Follows redirects. An HTTP error, no answer or a file over ``max_bytes`` is PHP's ``false``."""
    try:
        async with (
            httpx.AsyncClient(
                timeout=DOCUMENT_TIMEOUT, follow_redirects=True, transport=transport
            ) as client,
            client.stream("GET", url) as response,
        ):
            if response.status_code >= _FIRST_HTTP_ERROR:
                logger.warning(
                    "Document fetch refused | status={status}",
                    status=response.status_code,
                )
                return None
            return await _read_body(response, max_bytes)
    except (httpx.RequestError, httpx.InvalidURL) as exc:
        logger.warning("Document fetch failed | error={error}", error=type(exc).__name__)
        return None


async def _read_body(response: httpx.Response, max_bytes: int | None) -> bytes | None:
    body = bytearray()
    async for chunk in response.aiter_bytes():
        body += chunk
        if max_bytes is not None and len(body) > max_bytes:
            logger.warning("Document fetch too large | max_bytes={limit}", limit=max_bytes)
            return None
    return bytes(body)
