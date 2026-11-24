"""How long a BI Finance application waits for the bank (ТЗ, раздел 1: 3 рабочих дня)."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ALMATY = ZoneInfo("Asia/Almaty")
_SATURDAY = 5


def add_working_days(moment: datetime, days: int) -> datetime:
    """``moment`` plus ``days`` working days (Mon–Fri, Almaty), at the same time of day.

    Friday 10:00 + 3 → Wednesday 10:00. Created on a weekend, the count starts on Monday 00:00:
    Saturday + 3 → Thursday 00:00. Holidays are not counted yet (PLAN.md).
    """
    if days < 0:
        raise ValueError("days must not be negative")
    local = moment.astimezone(ALMATY)
    if local.weekday() >= _SATURDAY:
        local = (local + timedelta(days=7 - local.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    left = days
    while left > 0:
        local += timedelta(days=1)
        if local.weekday() < _SATURDAY:
            left -= 1
    return local
