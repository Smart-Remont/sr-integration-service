"""The BI Group request as PHP ``showroomInfoListAction`` makes it."""

import httpx
from loguru import logger

# Longer than the usual 30 s on purpose. PHP had no limit at all, the run is once a day and holds
# no connection to the database while it waits, and BI Group's real time to answer is unknown.
_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0)
# Added by httpx and absent from what PHP curl sends.
_LIBRARY_HEADERS = ("User-Agent", "Accept-Encoding", "Connection")


class ShowroomRequestError(Exception):
    """No answer from BI Group, the case when PHP ``curl_exec`` returns ``false``."""


class ShowroomClient:
    def __init__(self, url: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._url = url
        self._transport = transport

    async def send_list(self, body: bytes) -> bytes:
        """Any HTTP answer is returned as it is: PHP never looked at the status."""
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT, transport=self._transport) as client:
                for name in _LIBRARY_HEADERS:
                    del client.headers[name]
                response = await client.post(
                    self._url,
                    content=body,
                    headers={"Content-Type": "application/json"},
                )
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            raise ShowroomRequestError(type(exc).__name__) from exc
        if response.is_error:
            logger.warning("showroom-info-list: BI Group HTTP {code}", code=response.status_code)
        return response.content
