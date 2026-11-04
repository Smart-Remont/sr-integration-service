"""Kcell requests as PHP ``kcell_send_sms`` and ``kcell_check_batch_status`` make them.

Only the transport can fail here. Any HTTP answer is returned as it is: PHP looked at the body and
never at the status, and the service repeats that.
"""

import httpx
from loguru import logger

from src.config import KcellConfig
from src.php_compat.remote_file import HTTPX_HEADERS_PHP_DOES_NOT_SEND

# Far above the usual 30 s on purpose. An answer lost after Kcell accepted the batch leaves the
# messages in the queue, and the next run sends them again. The send-run lock keeps overlapping runs
# out, so a longer wait costs only delay. PHP had no limit at all.
_SEND_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0)
_STATUS_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


class KcellRequestError(Exception):
    """No answer from Kcell, the case when PHP ``curl_exec`` returns ``false``.

    The text is only the error class: nothing of the request gets into logs through it.
    """


class KcellClient:
    def __init__(
        self,
        config: KcellConfig,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._config = config
        self._transport = transport

    async def send_batch(self, body: bytes) -> bytes:
        return await self._request(
            "POST",
            self._config.send_url,
            auth=httpx.BasicAuth(self._config.send_username, self._config.send_password),
            time_limits=_SEND_TIMEOUT,
            content=body,
            headers={"Content-Type": "application/json"},
        )

    async def read_batch_status(self, batch_id: str) -> bytes:
        return await self._request(
            "GET",
            f"{self._config.status_url}/{batch_id}",
            auth=httpx.BasicAuth(self._config.status_username, self._config.status_password),
            time_limits=_STATUS_TIMEOUT,
        )

    async def _request(
        self,
        method: str,
        url: str,
        *,
        auth: httpx.BasicAuth,
        time_limits: httpx.Timeout,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> bytes:
        try:
            async with httpx.AsyncClient(timeout=time_limits, transport=self._transport) as client:
                for name in HTTPX_HEADERS_PHP_DOES_NOT_SEND:
                    del client.headers[name]
                response = await client.request(
                    method,
                    url,
                    auth=auth,
                    content=content,
                    headers=headers,
                )
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            raise KcellRequestError(type(exc).__name__) from exc
        if response.is_error:
            logger.warning("Kcell {method}: HTTP {code}", method=method, code=response.status_code)
        return response.content
