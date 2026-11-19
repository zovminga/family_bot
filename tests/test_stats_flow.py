"""StatsFlow seam: query state, conversion details, ConversationHandler states."""
from __future__ import annotations

import asyncio
from datetime import date, datetime
from types import SimpleNamespace
from typing import Any

from telegram.ext import CallbackQueryHandler

from family_bot import conversation_states as states
from family_bot import navigation as nav
from family_bot.flows import stats
from family_bot.sheet_data import Row
from tests.conftest import TEST_CURRENCY_CODES, FakeSheetsClient
from tests.known_bugs.fakes import FakeQuery, make_update

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


def make_flow(sheets: Any = None, get_rate: Any = None) -> stats.StatsFlow:
    return stats.StatsFlow(
        categories=lambda _ctx: ["Food", "Transport"],
        load_rows=lambda _ctx: list(ROWS),
        sheets=lambda _ctx: sheets,
        who=lambda _upd, _ctx: "Azat",
        all_codes=lambda: list(TEST_CURRENCY_CODES),
        get_rate=get_rate or (lambda src, dst: 1.0 if src == dst else 117.0),
    )


def replies(update: Any) -> list[str]:
    return [t for t, _ in update.message.replies]


def test_states_cover_every_stat_state() -> None:
    expected = {s for s in states.State if s.name.startswith("STAT_")}
    assert set(make_flow().states()) == expected


def test_specific_category_starts_fresh_query() -> None:
    flow, ctx = make_flow(), SimpleNamespace(user_data={})
    ctx.user_data[stats.QUERY_KEY] = stats.StatQuery(month="2026-09", convert_to="€")
    assert run(flow.on_category(make_update(stats.ONE_BUTTON), ctx)) == states.State.STAT_CAT
    assert run(flow.on_category(make_update("Food"), ctx)) == states.State.STAT_TYPE
    assert ctx.user_data[stats.QUERY_KEY] == stats.StatQuery(category="Food")


def test_conversion_details_then_home() -> None:
    flow, ctx = make_flow(), SimpleNamespace(user_data={})
    run(flow.begin(make_update("📊 Статистика"), ctx))
    run(flow.on_category(make_update(stats.ALL_BUTTON), ctx))
    run(flow.on_month(make_update("2026-10"), ctx))
    assert run(flow.on_group_currency(make_update(stats.NO_BUTTON), ctx)) == states.State.STAT_CONVERT_CURRENCY
    upd = make_update("дин")
    assert run(flow.on_convert_currency(upd, ctx)) == states.State.STAT_SHOW_DETAILS
    assert "за 2026-10" in replies(upd)[0] and "(в дин)" in replies(upd)[0]
    upd = make_update(stats.DETAILS_BUTTON)
    assert run(flow.on_show_details(upd, ctx)) == states.State.CHOOSE_ACTION
    assert "€ → дин" in replies(upd)[0], replies(upd)
    assert stats.QUERY_KEY not in ctx.user_data


def test_last_records_error_hides_raw_text() -> None:
    def broken_rows(_ctx: object) -> list[Row]:
        raise RuntimeError("APIError: [503] secret details")

    flow = stats.StatsFlow(
        categories=lambda _ctx: ["Food"],
        load_rows=broken_rows,
        sheets=lambda _ctx: None,
        who=lambda _upd, _ctx: "Azat",
        all_codes=lambda: list(TEST_CURRENCY_CODES),
        get_rate=lambda src, dst: 1.0,
    )
    ctx = SimpleNamespace(user_data={stats.QUERY_KEY: stats.StatQuery(category="Food")})
    upd = make_update(stats.LAST_BUTTON)
    run(flow.on_type(upd, ctx))
    text = replies(upd)[0]
    assert text.startswith("❌") and "503" not in text, text


def test_this_month_one_tap_summary(monkeypatch: Any) -> None:
    monkeypatch.setattr(stats.clock, "now", lambda: datetime(2026, 10, 19, 23, 30))
    flow, ctx = make_flow(), SimpleNamespace(user_data={})
    upd = make_update(nav.THIS_MONTH)
    assert run(flow.this_month(upd, ctx)) == states.State.CHOOSE_ACTION
    (text,) = replies(upd)
    assert text.startswith("📅 Октябрь 2026\n💰 Итого: 100 дин · 10 €"), text
    assert "Food — 100 дин" in text and "Azat — 10 €" in text


def test_this_month_error_hides_raw_text() -> None:
    def broken_rows(_ctx: object) -> list[Row]:
        raise RuntimeError("APIError: [503] secret details")

    flow = stats.StatsFlow(
        categories=lambda _ctx: ["Food"],
        load_rows=broken_rows,
        sheets=lambda _ctx: None,
        who=lambda _upd, _ctx: "Azat",
        all_codes=lambda: list(TEST_CURRENCY_CODES),
        get_rate=lambda src, dst: 1.0,
    )
    upd = make_update(nav.THIS_MONTH)
    assert run(flow.this_month(upd, SimpleNamespace(user_data={}))) == states.State.CHOOSE_ACTION
    text = replies(upd)[0]
    assert text.startswith("❌") and "503" not in text, text


# --- Conversion target: the same Currency picker as manual input (issue #34) ---

def _labels(markup: Any) -> dict[str, str]:
    return {b.text: b.callback_data for row in markup.inline_keyboard for b in row}


def _asked_for_target(sheets: Any = None, get_rate: Any = None) -> tuple[Any, Any, Any]:
    flow, ctx = make_flow(sheets, get_rate), SimpleNamespace(user_data={})
    ctx.user_data[stats.QUERY_KEY] = stats.StatQuery(month="2026-10")
    upd = make_update(stats.NO_BUTTON)
    assert run(flow.on_group_currency(upd, ctx)) == states.State.STAT_CONVERT_CURRENCY
    return flow, ctx, upd


def _tap(flow: stats.StatsFlow, ctx: Any, data: str) -> tuple[int, FakeQuery]:
    query = FakeQuery(data)
    return run(flow.on_convert_button(make_update(query=query), ctx)), query


def test_target_question_shows_recent_currencies_and_other() -> None:
    sheets = FakeSheetsClient()
    sheets.users[111] = ["CNY", "дин", "€"]
    _, _, upd = _asked_for_target(sheets)
    markup = upd.message.replies[-1][1]["reply_markup"]
    rows = [[b.text for b in row] for row in markup.inline_keyboard]
    assert rows[0] == ["🇨🇳 CNY", "🇷🇸 дин", "🇪🇺 €"]
    assert rows[1] == ["🌍 Другая"]
    assert _labels(markup)["🇨🇳 CNY"] == "scur:pick:CNY"


def test_other_page_then_pick_converts_into_any_currency() -> None:
    sheets = FakeSheetsClient()
    flow, ctx, _ = _asked_for_target(sheets)
    state, query = _tap(flow, ctx, "scur:page:0")
    assert state == states.State.STAT_CONVERT_CURRENCY
    assert query.edits[-1].startswith("🌍 Все валюты")
    gel = _labels(query.markups[-1])["🇬🇪 GEL"]
    state, query = _tap(flow, ctx, gel)
    assert state == states.State.STAT_SHOW_DETAILS
    assert query.edits == ["💱 Пересчёт в: 🇬🇪 GEL"]
    result = query.message.replies[0][0]
    assert "(в GEL)" in result and "💰 Итого: 12 870 GEL" in result, result
    assert ctx.user_data["recent_currencies"][0] == "GEL"
    assert sheets.users[111][0] == "GEL"


def test_typed_name_picks_the_target() -> None:
    flow, ctx, _ = _asked_for_target()
    upd = make_update("юань")
    assert run(flow.on_convert_currency(upd, ctx)) == states.State.STAT_SHOW_DETAILS
    assert "(в CNY)" in replies(upd)[0]


def test_typed_text_with_several_matches_offers_buttons() -> None:
    flow, ctx, _ = _asked_for_target()
    upd = make_update("долл")
    assert run(flow.on_convert_currency(upd, ctx)) == states.State.STAT_CONVERT_CURRENCY
    labels = _labels(upd.message.replies[-1][1]["reply_markup"])
    assert {"🇨🇦 CAD", "🇺🇸 $"} <= set(labels), labels


def test_unknown_typed_currency_asks_again() -> None:
    flow, ctx, _ = _asked_for_target()
    upd = make_update("xyz")
    assert run(flow.on_convert_currency(upd, ctx)) == states.State.STAT_CONVERT_CURRENCY
    text, kw = upd.message.replies[-1]
    assert text.startswith("❌") and "🌍 Другая" in _labels(kw["reply_markup"])


def test_missing_rate_is_said_plainly() -> None:
    flow, ctx, _ = _asked_for_target(get_rate=lambda src, dst: 1.0 if src == dst else None)
    state, query = _tap(flow, ctx, "scur:pick:RSD")
    assert state == states.State.CHOOSE_ACTION
    result = query.message.replies[0][0]
    assert "Нет курса для пересчёта в дин: €" in result, result
    assert "Итого" not in result


def test_picker_buttons_have_a_handler_in_target_state() -> None:
    patterns = [h.pattern for h in make_flow().states()[states.State.STAT_CONVERT_CURRENCY]
                if isinstance(h, CallbackQueryHandler)]
    assert any(p.match("scur:pick:RSD") for p in patterns)
    assert any(p.match("scur:page:2") for p in patterns)
