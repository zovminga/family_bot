"""StatsFlow seam: query state, conversion details, ConversationHandler states."""
from __future__ import annotations

import asyncio
from datetime import date
from types import SimpleNamespace
from typing import Any

from family_bot import conversation_states as states
from family_bot.flows import stats
from family_bot.sheet_data import Row
from tests.known_bugs.fakes import make_update

ROWS = [
    Row(date=date(2026, 10, 5), month="2026-10", category="Food", amount=100.0,
    currency="дин", who="Azat", comment=""),
    Row(date=date(2026, 10, 6), month="2026-10", category="Transport", amount=10.0,
    currency="€", who="Azat", comment=""),
]


def run(coro: Any) -> Any:
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def make_flow() -> stats.StatsFlow:
    return stats.StatsFlow(
        categories=lambda _ctx: ["Food", "Transport"],
        load_rows=lambda _ctx: list(ROWS),
        fallback_currencies=lambda _ctx: ["дин"],
        get_rate=lambda src, dst: 1.0 if src == dst else 117.0,
    )


def replies(update: Any) -> list[str]:
    return [t for t, _ in update.message.replies]


def test_states_cover_every_stat_state() -> None:
    expected = {s for s in states.State if s.name.startswith("STAT_")}
    assert set(make_flow().states()) == expected


def test_specific_category_starts_fresh_query() -> None:
    flow, ctx = make_flow(), SimpleNamespace(user_data={})
    ctx.user_data[stats.QUERY_KEY] = stats.StatQuery(month="2026-09", convert_to="€")
    assert run(flow.on_category(make_update("Specific category"), ctx)) == states.State.STAT_CAT
    assert run(flow.on_category(make_update("Food"), ctx)) == states.State.STAT_TYPE
    assert ctx.user_data[stats.QUERY_KEY] == stats.StatQuery(category="Food")


def test_conversion_details_then_home() -> None:
    flow, ctx = make_flow(), SimpleNamespace(user_data={})
    run(flow.begin(make_update("📊 Show statistics"), ctx))
    run(flow.on_category(make_update("All categories"), ctx))
    run(flow.on_month(make_update("2026-10"), ctx))
    assert run(flow.on_group_currency(make_update("No"), ctx)) == states.State.STAT_CONVERT_CURRENCY
    upd = make_update("дин")
    assert run(flow.on_convert_currency(upd, ctx)) == states.State.STAT_SHOW_DETAILS
    assert "for 2026-10" in replies(upd)[0] and "(in дин)" in replies(upd)[0]
    upd = make_update("Show details")
    assert run(flow.on_show_details(upd, ctx)) == states.State.CHOOSE_ACTION
    assert "€ → дин" in replies(upd)[0], replies(upd)
    assert stats.QUERY_KEY not in ctx.user_data


def test_last_records_error_hides_raw_text() -> None:
    def broken_rows(_ctx: object) -> list[Row]:
        raise RuntimeError("APIError: [503] secret details")

    flow = stats.StatsFlow(
        categories=lambda _ctx: ["Food"],
        load_rows=broken_rows,
        fallback_currencies=lambda _ctx: ["дин"],
        get_rate=lambda src, dst: 1.0,
    )
    ctx = SimpleNamespace(user_data={stats.QUERY_KEY: stats.StatQuery(category="Food")})
    upd = make_update("📜 Last 3 records")
    run(flow.on_type(upd, ctx))
    text = replies(upd)[0]
    assert text.startswith("❌") and "503" not in text, text
