"""Slow Sheets, FX or Claude calls must not freeze the bot for the other person (R11)."""
from __future__ import annotations

import asyncio
import sys
import time
from datetime import date
from types import SimpleNamespace
from typing import Any, Callable, Coroutine

import pytest

from family_bot import photo_input, voice_input
from family_bot.flows import stats
from family_bot.per_user import PerUserUpdateProcessor
from family_bot.sheet_data import Row
from tests.known_bugs.fakes import make_update

SLOW_SEC = 0.5
TICK_SEC = 0.1
ROWS = [
    Row(date=date(2026, 10, 5), month="2026-10", category="Food", amount=10.0,
    currency="€", who="Azat", comment=""),
]


def run(coro: Coroutine[Any, Any, Any]) -> Any:
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def ticks_during(work: Callable[[], Coroutine[Any, Any, Any]]) -> int:
    """Count event-loop ticks that happen while ``work`` runs."""
    ticks = 0
    done = False

    async def ticker() -> None:
        nonlocal ticks
        while not done:
            await asyncio.sleep(TICK_SEC)
            ticks += 1

    task = asyncio.create_task(ticker())
    await asyncio.sleep(0)
    await work()
    done = True
    await task
    return ticks


def test_stats_result_does_not_block_on_rows_and_fx() -> None:
    def slow_rows(_ctx: object) -> list[Row]:
        time.sleep(SLOW_SEC)
        return list(ROWS)

    def slow_rate(src: str, dst: str) -> float:
        time.sleep(SLOW_SEC)
        return 117.0

    flow = stats.StatsFlow(categories=lambda _ctx: ["Food"], load_rows=slow_rows,
                           fallback_currencies=lambda _ctx: ["дин"], get_rate=slow_rate)
    ctx = SimpleNamespace(user_data={stats.QUERY_KEY: stats.StatQuery(
        category="Food", month="2026-10", group_by_currency=False)})
    upd = make_update("дин")
    ticks = run(ticks_during(lambda: flow.on_convert_currency(upd, ctx)))
    assert ticks >= 5, "event loop frozen while loading rows / FX rate"
    assert "(in дин)" in upd.message.replies[0][0]


def test_updates_of_different_users_run_together_same_user_in_order() -> None:
    log: list[str] = []

    async def handle(name: str) -> None:
        log.append(f"start {name}")
        await asyncio.sleep(TICK_SEC)
        log.append(f"end {name}")

    def upd(user_id: int) -> Any:
        return SimpleNamespace(effective_user=SimpleNamespace(id=user_id))

    async def scenario() -> None:
        proc = PerUserUpdateProcessor()
        await asyncio.gather(
            proc.do_process_update(upd(1), handle("a1")),
            proc.do_process_update(upd(2), handle("b")),
            proc.do_process_update(upd(1), handle("a2")),
        )

    run(scenario())
    assert log.index("start b") < log.index("end a1"), log  # other person is not blocked
    assert log.index("end a1") < log.index("start a2"), log  # one person stays in order


class RecordingAnthropic:
    """Stands in for anthropic.Anthropic and records client options."""

    options: dict[str, Any] = {}

    def __init__(self, **kw: Any) -> None:
        RecordingAnthropic.options = kw
        self.messages = SimpleNamespace(create=lambda **_kw: SimpleNamespace(content=[
            SimpleNamespace(type="text", text='{"expenses": [], "note": ""}')]))


@pytest.mark.parametrize("call", [
    lambda: voice_input.extract_expenses("кофе 350", ["Food"], ["дин"], "01.11.2026", "A"),
    lambda: photo_input.extract_receipt(b"img", "image/jpeg", ["Food"], ["дин"],
                                        "01.11.2026", "A"),
], ids=["voice_and_text", "receipt_photo"])
def test_claude_has_short_timeout_and_one_retry(monkeypatch: pytest.MonkeyPatch,
                                                call: Callable[[], dict]) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(Anthropic=RecordingAnthropic))
    call()
    assert RecordingAnthropic.options == {"timeout": 30.0, "max_retries": 1}
