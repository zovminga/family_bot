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
from tests.known_bugs.fakes import FakeBot, FakeMessage, FakeQuery, make_update

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


# ----- ⚡ flow: a Preset without an amount asks it on a live card (#103) -----

@pytest.fixture
def now(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot.clock, "now", lambda: datetime(2026, 11, 20))


@pytest.fixture
def tg() -> FakeBot:
    return FakeBot()


def preset_ctx(sheets: FakeSheetsClient) -> Any:
    return bot_context(presets=[RENT, UTILITIES], sheets=sheets)


def in_chat(tg: FakeBot) -> list[FakeMessage]:
    """The bot's messages still in the chat."""
    return [m for m in tg.sent if not m.deleted]


def view(message: FakeMessage) -> tuple[str, list[str]]:
    """Text and inline button labels as the message shows now."""
    text, kw = message.edits[-1] if message.edits else (message.text, message.kw)
    markup = kw.get("reply_markup")
    return text, [b.text for row in markup.inline_keyboard for b in row]


def ask_utilities(ctx: Any, tg: FakeBot) -> FakeMessage:
    """Tap «⚡ Utilities»; returns the amount card."""
    assert run(bot.choose_preset(make_update("⚡ Utilities", bot=tg), ctx)) \
        == State.TYPING_PRESET_AMT
    [card] = in_chat(tg)
    return card


def test_preset_without_amount_asks_amount_on_a_card_and_writes_nothing(
        now: None, tg: FakeBot) -> None:
    sheets = FakeSheetsClient(data_values=[HEADERS])
    card = ask_utilities(preset_ctx(sheets), tg)
    assert sheets.appended_rows == []
    text, labels = view(card)
    assert text.splitlines()[:3] == ["⚡ <b>Utilities</b>", "Категория: <b>Bills</b>",
                                     "Кто: <b>Ann</b>"]
    assert text.endswith("Введите сумму (€):")
    assert labels == ["🏠 В начало"]


def test_typed_amount_turns_the_card_into_saved_line_with_undo(now: None, tg: FakeBot) -> None:
    sheets = FakeSheetsClient(data_values=[HEADERS])
    ctx = preset_ctx(sheets)
    card = ask_utilities(ctx, tg)
    update = make_update("1 234,5", bot=tg)

    assert run(bot.type_preset_amount(update, ctx)) == State.CHOOSE_ACTION
    assert update.message.deleted
    assert sheets.appended_rows == [
        ["20.11.2026", "2026-11", "Bills", 1234.5, "€", "Ann", "Utilities"]]
    text, labels = view(card)
    assert text == ("✅ Сохранено\n<blockquote><b>Utilities — 1 234,50 €</b>\n"
                    "Bills · 20.11 · Ann</blockquote>")
    assert labels == ["↩️ Отменить"]
    assert not card.deleted


@pytest.mark.parametrize("text", ["0", "-5", "abc", "1.234", "12 ххх"])
def test_bad_amount_is_deleted_and_card_shows_reason(now: None, tg: FakeBot, text: str) -> None:
    sheets = FakeSheetsClient(data_values=[HEADERS])
    ctx = preset_ctx(sheets)
    card = ask_utilities(ctx, tg)
    update = make_update(text, bot=tg)

    assert run(bot.type_preset_amount(update, ctx)) == State.TYPING_PRESET_AMT
    assert sheets.appended_rows == []
    assert update.message.deleted
    assert in_chat(tg) == [card]
    shown, _ = view(card)
    assert "❌ Нужна сумма больше нуля" in shown and shown.endswith("Введите сумму (€):")


def test_typed_currency_wins_over_sheet_currency(now: None, tg: FakeBot) -> None:
    sheets = FakeSheetsClient(data_values=[HEADERS])
    ctx = preset_ctx(sheets)
    ask_utilities(ctx, tg)

    run(bot.type_preset_amount(make_update("3500 дин", bot=tg), ctx))
    assert sheets.appended_rows[0][3:5] == [3500.0, "дин"]


def test_home_on_amount_step_removes_the_card(now: None, tg: FakeBot) -> None:
    from family_bot import navigation as nav

    sheets = FakeSheetsClient(data_values=[HEADERS])
    ctx = preset_ctx(sheets)
    card = ask_utilities(ctx, tg)

    query = FakeQuery(nav.HOME_CALLBACK, message=card)
    assert run(nav.on_home(make_update(query=query), ctx)) == State.CHOOSE_ACTION
    assert card.deleted
    assert ctx.user_data == {}
    assert sheets.appended_rows == []


def test_preset_with_amount_saves_at_once_with_undo(now: None) -> None:
    sheets = FakeSheetsClient(data_values=[HEADERS])
    ctx = preset_ctx(sheets)
    update = make_update("⚡ Rent")

    assert run(bot.choose_preset(update, ctx)) == State.CHOOSE_ACTION
    assert sheets.appended_rows == [
        ["20.11.2026", "2026-11", "Rent", 850.0, "€", "Both", "Rent"]]
    text, kw = update.message.replies[0]
    assert text.startswith("✅ Сохранено\n<blockquote><b>Rent — 850 €</b>")
    assert [b.text for row in kw["reply_markup"].inline_keyboard for b in row] \
        == ["↩️ Отменить"]
