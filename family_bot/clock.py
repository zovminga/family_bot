"""Family wall clock: the current time in the household's time zone.

The bot host (Render) runs in UTC, so a plain ``datetime.now()`` gives
yesterday's date for an expense entered after midnight in Belgrade. Every
place that needs "now" or "today" calls ``clock.now()`` instead.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

TZ_ENV = "TZ_NAME"
DEFAULT_TZ = "Europe/Belgrade"


def family_tz() -> ZoneInfo:
    """Return the zone named by ``TZ_NAME`` (default ``Europe/Belgrade``)."""
    return ZoneInfo(os.getenv(TZ_ENV) or DEFAULT_TZ)


def now(utc_now: Optional[datetime] = None) -> datetime:
    """
    Current wall-clock time in the family time zone, as a naive datetime.

    Parameters
    ----------
    utc_now : datetime, optional
        Aware instant to convert (tests). Defaults to the real current time.

    Returns
    -------
    datetime
        Naive local time, safe to mix with naive dates parsed from the sheet.
    """
    instant = utc_now or datetime.now(timezone.utc)
    return instant.astimezone(family_tz()).replace(tzinfo=None)
