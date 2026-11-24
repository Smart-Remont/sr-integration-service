"""The BI Finance access token, cached in integration_token_tab: the gateway answers 429 to
frequent token requests (ТЗ, раздел 2)."""

from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from loguru import logger

from ..repo import InstallmentProvider, InstallmentRepository
from .client import BIFClient, BIFClientError, BIFToken

DEFAULT_TOKEN_TTL_SECONDS = 300
_EXPIRY_MARGIN = timedelta(seconds=30)


class BIFTokenSource:
    def __init__(self, repository: InstallmentRepository, client: BIFClient, app_env: str) -> None:
        self.repository = repository
        self.client = client
        self.app_env = app_env

    async def access_token(self, provider: InstallmentProvider, *, renew: bool = False) -> str:
        """A cached token while it lives; then the refresh token; then a new login.
        ``renew`` — the gateway refused the cached one (401)."""
        cached = await self.repository.get_token(provider.id)
        now = datetime.now(UTC)
        if not renew and cached is not None and cached.expires_at > now + _EXPIRY_MARGIN:
            return cached.access_token
        if cached is not None and cached.refresh_token:
            try:
                token = await self.client.refresh_token(
                    provider.base_url,
                    cached.refresh_token,
                    timeout_seconds=timeout_seconds(provider),
                )
                return await self._store(provider, token)
            except BIFClientError as exc:
                if exc.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
                    raise _too_many_requests() from exc
                logger.info("BIF token refresh refused, logging in | status={s}", s=exc.status_code)
        return await self._login(provider)

    async def _login(self, provider: InstallmentProvider) -> str:
        credentials = await self.repository.get_active_credentials(provider.id, self.app_env)
        if credentials is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Нет учётки BI Finance (clientId/clientSecret) для env={self.app_env}.",
            )
        try:
            token = await self.client.issue_token(
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
        return await self._store(provider, token)

    async def _store(self, provider: InstallmentProvider, token: BIFToken) -> str:
        now = datetime.now(UTC)
        expires_at = self.client.access_token_expires_at(token.access_token)
        if token.expires_in is not None:
            expires_at = now + timedelta(seconds=token.expires_in)
        if expires_at is None or expires_at <= now:
            ttl = provider.config.get("token_ttl_seconds", DEFAULT_TOKEN_TTL_SECONDS)
            expires_at = now + timedelta(seconds=int(ttl))
        # Committed: a rolled-back request must not throw away a token the gateway already
        # issued, or the next call logs in again and runs into 429.
        await self.repository.committed(
            lambda repo: repo.upsert_token(
                provider_id=provider.id,
                access_token=token.access_token,
                refresh_token=token.refresh_token,
                expires_at=expires_at,
            )
        )
        return token.access_token


def timeout_seconds(provider: InstallmentProvider) -> float:
    value = provider.config.get("timeout_seconds")
    return float(value) if isinstance(value, int | float) and value > 0 else 30.0


def _too_many_requests() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="BI Finance: слишком частые запросы, повторите через минуту.",
    )
