from decimal import Decimal, InvalidOperation
from typing import Any

DEFAULT_PRINCIPAL_MIN = Decimal("5000")
DEFAULT_PRINCIPAL_MAX = Decimal("3000000")


def _parse_decimal(value: Any, default: Decimal) -> Decimal:
    if value is None:
        return default
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return default
    if parsed <= 0:
        return default
    return parsed


def parse_principal_limits(config: dict[str, Any]) -> tuple[Decimal, Decimal]:
    """Product limits for FACTORING_SR from integration_provider_tab.config."""
    principal_min = _parse_decimal(config.get("principal_min"), DEFAULT_PRINCIPAL_MIN)
    principal_max = _parse_decimal(config.get("principal_max"), DEFAULT_PRINCIPAL_MAX)
    if principal_min > principal_max:
        return DEFAULT_PRINCIPAL_MIN, DEFAULT_PRINCIPAL_MAX
    return principal_min, principal_max


def phone_for_prescoring(normalized_phone: str) -> str | None:
    """Bank prescoring expects 11 characters: 7 plus 10 digits (77057238447)."""
    digits = "".join(symbol for symbol in normalized_phone if symbol.isdigit())
    if digits.startswith("8") and len(digits) == 11:
        digits = "7" + digits[1:]
    if len(digits) == 10:
        digits = f"7{digits}"
    if len(digits) == 11 and digits.startswith("7"):
        return digits
    return None


def parse_prescoring_max_limit(raw: Any) -> Decimal | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        value = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    if value <= 0:
        return None
    return value
