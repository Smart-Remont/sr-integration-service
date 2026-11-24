"""The BI Finance access token, cached in integration_token_tab: the gateway answers 429 to
frequent token requests (ТЗ, раздел 2)."""

from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from loguru import logger

from ..repo import InstallmentProvider, InstallmentRepository, ProviderToken
from .client import BIFClient, BIFClientError, BIFToken

DEFAULT_TOKEN_TTL_SECONDS = 300
# A create with one repeat must fit the CRM proxy's 30 s: 2 × 12 s + the pause and the token.
DEFAULT_TIMEOUT_SECONDS = 12.0
_EXPIRY_MARGIN = timedelta(seconds=30)


class BIFTokenSource:
    def __init__(self, repository: InstallmentRepository, client: BIFClient, app_env: str) -> None:
        self.repository = repository
        self.client = client
        self.app_env = app_env

    async def access_token(
        self, provider: InstallmentProvider, *, rejected: str | None = None
    ) -> str:
        """The cached token while it lives; else a renewal. ``rejected`` — the token the gateway
        just answered 401 to: it is not handed out again."""
        cached = await self.repository.get_token(provider.id)
        if cached is not None and _usable(cached, rejected):
            return cached.access_token
        # One transaction under an advisory lock: workers that need a token at the same time
        # wait for the first one's renewal instead of all logging in (429). Committed: a
        # rolled-back request must not throw away a token the gateway already issued.
        return await self.repository.committed(lambda repo: self._renew(repo, provider, rejected))

    async def _renew(
        self, repo: InstallmentRepository, provider: InstallmentProvider, rejected: str | None
    ) -> str:
        await repo.lock_token_renewal(provider.id)
        cached = await repo.get_token(provider.id)
        if cached is not None and _usable(cached, rejected):
            return cached.access_token
        token: BIFToken | None = None
        if cached is not None and cached.refresh_token:
            try:
                token = await self.client.refresh_token(
                    provider.base_url,
                    cached.refresh_token,
                    timeout_seconds=timeout_seconds(provider),
                )
            except BIFClientError as exc:
                if exc.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
                    raise _too_many_requests() from exc
                logger.info("BIF token refresh refused, logging in | status={s}", s=exc.status_code)
        if token is None:
            token = await self._login(repo, provider)
        await repo.upsert_token(
            provider_id=provider.id,
            access_token=token.access_token,
            refresh_token=token.refresh_token,
            expires_at=self._expires_at(provider, token),
        )
        return token.access_token

    async def _login(self, repo: InstallmentRepository, provider: InstallmentProvider) -> BIFToken:
        credentials = await repo.get_active_credentials(provider.id, self.app_env)
        if credentials is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Нет учётки BI Finance (clientId/clientSecret) для env={self.app_env}.",
            )
        try:
            return await self.client.issue_token(
                provider.base_url,
                credentials.username,
                credentials.password,
                timeout_seconds=timeout_seconds(provider),
            )
        except BIFClientError as exc:
            if exc.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
                raise _too_many_requests() from exc
            if exc.status_code == status.HTTP_401_UNAUTHORIZED:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="BI Finance не принял clientId/clientSecret.",
                ) from exc
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"BI Finance: сервис авторизации недоступен ({exc.message}).",
            ) from exc

    def _expires_at(self, provider: InstallmentProvider, token: BIFToken) -> datetime:
        now = datetime.now(UTC)
        expires_at = self.client.access_token_expires_at(token.access_token)
        if token.expires_in is not None:
            expires_at = now + timedelta(seconds=token.expires_in)
        if expires_at is None or expires_at <= now:
            ttl = provider.config.get("token_ttl_seconds", DEFAULT_TOKEN_TTL_SECONDS)
            expires_at = now + timedelta(seconds=int(ttl))
        return expires_at


def _usable(cached: ProviderToken, rejected: str | None) -> bool:
    return (
        cached.access_token != rejected and cached.expires_at > datetime.now(UTC) + _EXPIRY_MARGIN
    )


def timeout_seconds(provider: InstallmentProvider) -> float:
    value = provider.config.get("timeout_seconds")
    return float(value) if isinstance(value, int | float) and value > 0 else DEFAULT_TIMEOUT_SECONDS


def _too_many_requests() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="BI Finance: слишком частые запросы, повторите через минуту.",
    )
