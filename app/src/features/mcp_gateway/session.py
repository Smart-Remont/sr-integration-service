"""The caller of one ``/mcp`` HTTP request: his MySpace token, renewed once if MySpace drops it.

The gate puts it into the ASGI scope state; the SDK hands the tools the same Starlette request.
"""

import asyncio
from typing import Any

from loguru import logger
from starlette.requests import Request

from .client import ExtTokenRejected, MyspaceExtClient
from .tokens import Grant, TokenBroker, log_id

CALLER_STATE_KEY = "mcp_gateway_caller"
# Exchange, read and the one retry of a request together, under nginx's 30 s proxy timeout.
CALL_DEADLINE_SECONDS = 25.0


def request_deadline() -> float:
    """For ``asyncio.timeout_at``: the end of a request started now."""
    return asyncio.get_running_loop().time() + CALL_DEADLINE_SECONDS


class CallerSession:
    def __init__(
        self,
        *,
        client: MyspaceExtClient,
        broker: TokenBroker,
        subject_token: str,
        key: str,
        grant: Grant,
        deadline: float,
    ) -> None:
        self._client = client
        self._broker = broker
        self._subject_token = subject_token
        self._key = key
        self._grant = grant
        self.deadline = deadline

    @property
    def scopes(self) -> frozenset[str]:
        return self._grant.scopes

    @property
    def log_id(self) -> str:
        return log_id(self._key)

    async def read(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        """A 401 means the kept token died before its time: one new exchange, then the error."""
        dead_access_token = self._grant.access_token
        try:
            return await self._client.get(path, dead_access_token, params)
        except ExtTokenRejected:
            logger.info(
                "mcp_gateway: 401 for caller {caller}, exchanging again", caller=self.log_id
            )
        self._grant = await self._broker.refresh(self._subject_token, self._key, dead_access_token)
        return await self._client.get(path, self._grant.access_token, params)


def caller_of(request: Any) -> CallerSession | None:
    if not isinstance(request, Request):
        return None
    caller = request.scope.get("state", {}).get(CALLER_STATE_KEY)
    return caller if isinstance(caller, CallerSession) else None
