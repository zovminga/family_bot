"""Undo button wiring in the bot (issue #31): manual save and Draft save."""
from __future__ import annotations

import asyncio
from datetime import datetime

from family_bot import bot, draft_expense as draft, undo
from family_bot.conversation_states import State
from tests.conftest import FakeSheetsClient, bot_context
from tests.known_bugs.fakes import FakeQuery, make_update

HEADERS = ["Date", "Month", "Category", "Amount", "Currency", "Who", "Comment"]


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _labels(markup):
    return [(b.text, b.callback_data) for row in markup.inline_keyboard for b in row]


def _ctx(sheets):
    ctx = bot_context(sheets=sheets, currencies=["дин", "€"])
    return ctx


def test_manual_save_offers_undo_that_deletes_that_row(monkeypatch):
    from family_bot.flows import manual

    sheets = FakeSheetsClient(data_values=[HEADERS])
    ctx = _ctx(sheets)
    monkeypatch.setattr(manual.clock, "now", lambda: datetime(2026, 11, 19, 12))
    ctx.user_data.update(cat="Food", amt=350.0, cur="дин", spender="Azat", comment="")
    query = FakeQuery("today")
    assert run(bot.MANUAL.choose_dt(make_update(query=query), ctx)) == State.CHOOSE_ACTION
    [(label, data)] = _labels(query.markups[0])
    assert label == "↩️ Отменить"
    assert undo.KEY_LAST_SAVE in ctx.user_data  # go_home keeps it

    sheets.append_data_row(["19.11.2026", "2026-11", "Food", 90, "дин", "Lisa", "later"])
    monkeypatch.setattr(bot.clock, "now", lambda: datetime(2026, 11, 19, 12, 5))
    tap = FakeQuery(data)
    run(bot.undo_callback(make_update(query=tap), ctx))
    assert tap.edits == ["↩️ Отменено, запись удалена: Food — 350 дин, 19.11.2026"]
    assert [r[6] for r in sheets.read_data_values()[1:]] == ["later"]


def test_typed_date_save_also_offers_undo():
    ctx = _ctx(FakeSheetsClient())
    ctx.user_data.update(cat="Food", amt=5.0, cur="€", spender="Azat", comment="x")
    upd = make_update("13.07.2025")
    run(bot.MANUAL.type_dt(upd, ctx))
    _, kw = upd.message.replies[0]
    assert _labels(kw["reply_markup"])[0][0] == "↩️ Отменить"


def test_other_users_tap_gets_alert_and_keeps_the_button(monkeypatch):
    sheets = FakeSheetsClient(data_values=[HEADERS])
    owner = _ctx(sheets)
    monkeypatch.setattr(bot.clock, "now", lambda: datetime(2026, 11, 19, 12))
    [[(_, data)]] = undo.remember(owner.user_data, [], datetime(2026, 11, 19, 12))
    stranger = bot_context(sheets=sheets)
    stranger.bot_data = owner.bot_data
    tap = FakeQuery(data)
    run(bot.undo_callback(make_update(query=tap, user_id=222), stranger))
    assert tap.edits == []
    assert tap.answers[-1][0] == "Отменить можно только свою последнюю запись."
    assert undo.KEY_LAST_SAVE in owner.user_data


def test_draft_save_offers_undo(monkeypatch):
    sheets = FakeSheetsClient(data_values=[HEADERS])
    ctx = _ctx(sheets)
    monkeypatch.setattr(bot.clock, "now", lambda: datetime(2026, 11, 19, 12))
    session = draft.add_draft(ctx.user_data, draft.DraftSession(items=[{
        "date": "19.11.2026", "category": "Food", "amount": 350.0, "currency": "дин",
        "who": "Azat", "comment": "кофе"}]))
    save = FakeQuery(f"vx:{session.id}:save")
    run(bot.vx_callback(make_update(query=save), ctx))
    assert save.edits[0].startswith("✅ Сохранено")
    [(label, data)] = _labels(save.markups[0])
    assert label == "↩️ Отменить"

    tap = FakeQuery(data)
    run(bot.undo_callback(make_update(query=tap), ctx))
    assert tap.edits[0].startswith("↩️ Отменено")
    assert sheets.read_data_values() == [HEADERS]


def test_undo_handler_is_registered_before_the_conversation(monkeypatch):
    app = bot.build_application("123:abc")
    group0 = app.handlers[0]
    patterns = [getattr(h, "pattern", None) for h in group0]
    undo_at = next(i for i, p in enumerate(patterns) if p is not None and p.pattern == "^undo:")
    conv_at = next(i for i, h in enumerate(group0) if type(h).__name__ == "ConversationHandler")
    assert undo_at < conv_at
