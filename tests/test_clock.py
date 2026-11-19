"""Tests for the family-time-zone clock."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

import clock


def test_after_midnight_belgrade_is_next_day_vs_utc(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TZ_NAME", raising=False)
    # 22:30 UTC on 14 Jul is 00:30 CEST on 15 Jul in Belgrade.
    utc = datetime(2026, 7, 14, 22, 30, tzinfo=timezone.utc)
    local = clock.now(utc)
    assert (local.year, local.month, local.day, local.hour, local.minute) == (2026, 7, 15, 0, 30)
    assert local.tzinfo is None


def test_winter_offset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TZ_NAME", raising=False)
    utc = datetime(2026, 1, 14, 23, 30, tzinfo=timezone.utc)
    assert clock.now(utc) == datetime(2026, 1, 15, 0, 30)


def test_tz_name_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TZ_NAME", "UTC")
    utc = datetime(2026, 7, 14, 22, 30, tzinfo=timezone.utc)
    assert clock.now(utc) == datetime(2026, 7, 14, 22, 30)


def test_now_without_argument_is_family_wall_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TZ_NAME", raising=False)
    before = clock.now(datetime.now(timezone.utc))
    got = clock.now()
    assert abs((got - before).total_seconds()) < 5
