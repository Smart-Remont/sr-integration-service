"""The exchanged MySpace token of each caller, kept in the memory of this worker.

Key: sha256 of the caller's token, so the cache never holds it. A token is kept until ``expires_in``
minus a margin, then exchanged again. Each gunicorn worker has its own cache: at worst one exchange
per caller per worker every few minutes.
"""

import asyncio
import hashlib
import time
import weakref
from collections.abc import Callable
from dataclasses import dataclass

from loguru import logger

from .client import ExchangeRefused, IssuedToken, MyspaceExtClient

# Ends the use of a token before MySpace does: a call started just before the end still gets through.
EXPIRY_MARGIN_SECONDS = 30
# One entry is a few hundred bytes; far above the number of employees, it only stops runaway growth.
MAX_ENTRIES = 10_000
# A refused token is answered from memory this long, so a retrying client does not hammer MySpace.
REFUSAL_TTL_SECONDS = 30


def token_key(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def log_id(key: str) -> str:
    """What the log may show of a caller: a prefix of the hash, never the token."""
    return key[:12]


@dataclass(frozen=True)
class Grant:
    access_token: str
    scopes: frozenset[str]
    expires_at: float


class _BoundedExpiringMap[V]:
    """Entries with an end time; the oldest goes first when full (dicts keep insertion order)."""

    def __init__(self, clock: Callable[[], float], max_entries: int) -> None:
        self._clock = clock
        self._max_entries = max_entries
        self._entries: dict[str, tuple[float, V]] = {}

    def get(self, key: str) -> V | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if entry[0] <= self._clock():
            del self._entries[key]
            return None
        return entry[1]

    def put(self, key: str, value: V, expires_at: float) -> None:
        now = self._clock()
        for old in [k for k, (end, _) in self._entries.items() if end <= now]:
            del self._entries[old]
        self._entries.pop(key, None)
        while len(self._entries) >= self._max_entries:
            del self._entries[next(iter(self._entries))]
        self._entries[key] = (expires_at, value)

    def drop(self, key: str) -> None:
        self._entries.pop(key, None)

    def __len__(self) -> int:
        return len(self._entries)


class TokenCache:
    def __init__(
        self, clock: Callable[[], float] = time.monotonic, max_entries: int = MAX_ENTRIES
    ) -> None:
        self._clock = clock
        self._grants: _BoundedExpiringMap[Grant] = _BoundedExpiringMap(clock, max_entries)

    def get(self, key: str) -> Grant | None:
        return self._grants.get(key)

    def put(self, key: str, issued: IssuedToken) -> Grant:
        """A token about to end is used for this request and not kept."""
        grant = Grant(
            access_token=issued.access_token,
            scopes=issued.scopes,
            expires_at=self._clock() + issued.expires_in - EXPIRY_MARGIN_SECONDS,
        )
        if issued.expires_in > EXPIRY_MARGIN_SECONDS:
            self._grants.put(key, grant, grant.expires_at)
        return grant

    def drop(self, key: str) -> None:
        self._grants.drop(key)

    def __len__(self) -> int:
        return len(self._grants)


class RefusalCache:
    """Tokens MySpace refused a moment ago, with the refusal to repeat."""

    def __init__(
        self, clock: Callable[[], float] = time.monotonic, max_entries: int = MAX_ENTRIES
    ) -> None:
        self._clock = clock
        self._refusals: _BoundedExpiringMap[ExchangeRefused] = _BoundedExpiringMap(
            clock, max_entries
        )

    def get(self, key: str) -> ExchangeRefused | None:
        return self._refusals.get(key)

    def put(self, key: str, refusal: ExchangeRefused) -> None:
        self._refusals.put(key, refusal, self._clock() + REFUSAL_TTL_SECONDS)


class TokenBroker:
    """Cache first, exchange on a miss. One caller's exchanges go one at a time."""

    def __init__(
        self,
        client: MyspaceExtClient,
        cache: TokenCache,
        refusals: RefusalCache,
        scopes: frozenset[str],
    ) -> None:
        self._client = client
        self._cache = cache
        self._refusals = refusals
        self._scopes = scopes
        self._locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()

    async def grant_for(self, subject_token: str, key: str) -> Grant:
        grant = self._cache.get(key)
        if grant is not None:
            return grant
        async with self._lock(key):
            grant = self._cache.get(key)
            if grant is not None:
                return grant
            return await self._exchange(subject_token, key)

    async def refresh(self, subject_token: str, key: str, dead_access_token: str) -> Grant:
        """After a 401 on a read. Another request of this caller may have renewed it already."""
        async with self._lock(key):
            grant = self._cache.get(key)
            if grant is not None and grant.access_token != dead_access_token:
                return grant
            self._cache.drop(key)
            return await self._exchange(subject_token, key)

    def _lock(self, key: str) -> asyncio.Lock:
        # Weak values: the lock lives while a request of this caller holds or waits for it.
        return self._locks.setdefault(key, asyncio.Lock())

    async def _exchange(self, subject_token: str, key: str) -> Grant:
        refusal = self._refusals.get(key)
        if refusal is not None:
            # A new exception each time: re-raising one instance keeps growing its traceback.
            raise type(refusal)(refusal.status, refusal.code)
        try:
            issued = await self._client.exchange(subject_token, self._scopes)
        except ExchangeRefused as exc:
            self._refusals.put(key, exc)
            raise
        logger.info(
            "mcp_gateway: exchanged token of caller {caller}, scopes {scopes}, expires in {ttl} s",
            caller=log_id(key),
            scopes=" ".join(sorted(issued.scopes)),
            ttl=issued.expires_in,
        )
        return self._cache.put(key, issued)
