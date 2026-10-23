from __future__ import annotations

from .token import IntegrationContext


def employee_id_from_context(
    context: IntegrationContext | None,
    *,
    fallback: int,
) -> int:
    if context is None:
        return fallback
    return context.employee_id
