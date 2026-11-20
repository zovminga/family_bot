#!/usr/bin/env python3
"""Preset without an amount (issue #85): a monthly Regular_expenses row with a
blank amount becomes a ⚡ Preset that asks the amount before saving."""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import pytest

from family_bot import bot, regular
from family_bot.conversation_states import State
from tests.conftest import FakeSheetsClient, bot_context
from tests.known_bugs.fakes import make_update

HEADERS = ["Date", "Month", "Category", "Amount", "Currency", "Who", "Comment"]
GRID = [
    ["название", "динары", "евро", "категория", "кто"],
    ["ежемесячные траты", "", "", "", ""],
    ["Rent", "", "850", "Rent", "Both"],
    ["Utilities", "", "", "Bills", "Ann"],
    ["Phone", "?", "", "Bills", "Bob"],
    ["разовые траты", "", "", "", ""],
    ["Sauna", "", "", "Beauty", "Both"],
]
UTILITIES = {"name": "Utilities", "amount": None, "currency": "€",
             "category": "Bills", "who": "Ann"}
RENT = {"name": "Rent", "amount": 850.0, "currency": "€",
        "category": "Rent", "who": "Both"}


def run(coro: Any) -> Any:
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ----- parse_regular -----

def test_monthly_row_without_amount_is_a_preset_in_base_currency() -> None:
    parsed = regular.parse_regular(GRID, base_currency="€")
    assert parsed["monthly"][1] == UTILITIES


def test_monthly_row_with_a_mark_in_a_currency_column_takes_that_currency() -> None:
    parsed = regular.parse_regular(GRID, base_currency="€")
    assert parsed["monthly"][2] == {"name": "Phone", "amount": None, "currency": "дин",
                                    "category": "Bills", "who": "Bob"}


def test_oneoff_row_without_amount_is_still_skipped() -> None:
    assert regular.parse_regular(GRID, base_currency="€")["oneoff"] == []


def test_preset_with_amount_is_unchanged() -> None:
    assert regular.parse_regular(GRID, base_currency="₽")["monthly"][0] == RENT


def test_load_presets_passes_base_currency() -> None:
    items = regular.load_presets(FakeSheetsClient(regular_values=GRID), base_currency="дин")
    assert items[1]["currency"] == "дин"
    assert items[1]["amount"] is None


def test_config_reload_uses_base_currency() -> None:
    from family_bot import config

    cfg = config.Config(sheets=FakeSheetsClient(regular_values=GRID), default_currency="$")
    config.reload(cfg)
    assert cfg.presets[1]["currency"] == "$"


# ----- ⚡ flow -----

@pytest.fixture
def now(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot.clock, "now", lambda: datetime(2026, 11, 20))


def preset_ctx(sheets: FakeSheetsClient) -> Any:
    return bot_context(presets=[RENT, UTILITIES], sheets=sheets)


def test_preset_without_amount_asks_amount_and_writes_nothing(now: None) -> None:
    sheets = FakeSheetsClient(data_values=[HEADERS])
    ctx = preset_ctx(sheets)
    update = make_update("⚡ Utilities")

    assert run(bot.choose_preset(update, ctx)) == State.TYPING_PRESET_AMT
    assert sheets.appended_rows == []
    text, kw = update.message.replies[-1]
    assert "Utilities" in text
    assert "🏠 В начало" in [b.text for row in kw["reply_markup"].keyboard for b in row]


def test_typed_amount_saves_preset_with_name_as_comment(now: None) -> None:
    sheets = FakeSheetsClient(data_values=[HEADERS])
    ctx = preset_ctx(sheets)
    run(bot.choose_preset(make_update("⚡ Utilities"), ctx))
    update = make_update("1 234,5")

    assert run(bot.type_preset_amount(update, ctx)) == State.CHOOSE_ACTION
    assert sheets.appended_rows == [
        ["20.11.2026", "2026-11", "Bills", 1234.5, "€", "Ann", "Utilities"]]
    assert ("✅ Сохранено: Utilities — 1 234,50 € · 20.11.2026"
            in [text for text, _ in update.message.replies])


@pytest.mark.parametrize("text", ["0", "-5", "abc", "1.234", "12 ххх"])
def test_bad_amount_asks_again(now: None, text: str) -> None:
    sheets = FakeSheetsClient(data_values=[HEADERS])
    ctx = preset_ctx(sheets)
    run(bot.choose_preset(make_update("⚡ Utilities"), ctx))
    update = make_update(text)

    assert run(bot.type_preset_amount(update, ctx)) == State.TYPING_PRESET_AMT
    assert sheets.appended_rows == []
    assert update.message.replies[-1][0].startswith("❌ Нужна сумма больше нуля")


def test_typed_currency_wins_over_sheet_currency(now: None) -> None:
    sheets = FakeSheetsClient(data_values=[HEADERS])
    ctx = preset_ctx(sheets)
    run(bot.choose_preset(make_update("⚡ Utilities"), ctx))

    run(bot.type_preset_amount(make_update("3500 дин"), ctx))
    assert sheets.appended_rows[0][3:5] == [3500.0, "дин"]


def test_home_on_amount_step_leaves_nothing_behind(now: None) -> None:
    from family_bot import navigation as nav

    sheets = FakeSheetsClient(data_values=[HEADERS])
    ctx = preset_ctx(sheets)
    run(bot.choose_preset(make_update("⚡ Utilities"), ctx))

    assert run(nav.on_home(make_update(nav.HOME_BUTTON), ctx)) == State.CHOOSE_ACTION
    assert ctx.user_data == {}
    assert sheets.appended_rows == []


def test_preset_with_amount_saves_at_once(now: None) -> None:
    sheets = FakeSheetsClient(data_values=[HEADERS])
    ctx = preset_ctx(sheets)

    assert run(bot.choose_preset(make_update("⚡ Rent"), ctx)) == State.CHOOSE_ACTION
    assert sheets.appended_rows == [
        ["20.11.2026", "2026-11", "Rent", 850.0, "€", "Both", "Rent"]]
