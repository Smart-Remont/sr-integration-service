from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from src.config import integration_context_config

from .constants import INTEGRATION_CONTEXT_HEADER
from .token import IntegrationContext, verify_integration_context


async def get_integration_context(
    x_integration_context: Annotated[
        str | None,
        Header(alias=INTEGRATION_CONTEXT_HEADER),
    ] = None,
) -> IntegrationContext | None:
    if not integration_context_config.is_enforced:
        return None
    if not x_integration_context or not x_integration_context.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Missing {INTEGRATION_CONTEXT_HEADER} header.",
        )
    try:
        return verify_integration_context(
            x_integration_context.strip(),
            secret=integration_context_config.secret,
            audience=integration_context_config.audience,
            issuers=integration_context_config.issuer_allowlist,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid integration context token.",
        ) from exc


IntegrationContextDep = Annotated[IntegrationContext | None, Depends(get_integration_context)]
