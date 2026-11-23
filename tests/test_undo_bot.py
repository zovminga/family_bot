"""Undo button wiring in the bot (issue #31): manual save and Draft save."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from family_bot import bot, draft_expense as draft, undo
from family_bot.conversation_states import State
from tests.conftest import FakeSheetsClient, bot_context
from tests.known_bugs.fakes import FakeMessage, FakeQuery, make_update

HEADERS = ["Date", "Month", "Category", "Amount", "Currency", "Who", "Comment"]


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _labels(markup):
    return [(b.text, b.callback_data, b.style) for row in markup.inline_keyboard for b in row]


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
    [(label, data, style)] = _labels(query.markups[0])
    assert (label, style) == ("↩️ Отменить", "danger")
    assert undo.KEY_LAST_SAVE in ctx.user_data  # go_home keeps it

    sheets.append_data_row(["19.11.2026", "2026-11", "Food", 90, "дин", "Lisa", "later"])
    monkeypatch.setattr(bot.clock, "now", lambda: datetime(2026, 11, 19, 12, 5))
    tap = FakeQuery(data)
    run(bot.undo_callback(make_update(query=tap), ctx))
    assert tap.edits == ["↩️ Отменено\n<blockquote><s>Food — 350 дин</s>\n"
                         "19.11 · Azat</blockquote>"]
    assert tap.markups == [None]  # no buttons left
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
    [[(_, data, _)]] = undo.remember(owner.user_data, [], datetime(2026, 11, 19, 12))
    stranger = bot_context(sheets=sheets)
    stranger.bot_data = owner.bot_data
    tap = FakeQuery(data)
    run(bot.undo_callback(make_update(query=tap, user_id=222), stranger))
    assert tap.edits == []
    assert tap.answers[-1][0].startswith("Отменить можно только свою последнюю трату")
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
    assert save.edits == ["✅ Сохранено\n<blockquote><b>кофе — 350 дин</b>\n"
                          "Food · 19.11 · Azat</blockquote>"]
    [(label, data, style)] = _labels(save.markups[0])
    assert (label, style) == ("↩️ Отменить", "danger")

    tap = FakeQuery(data)
    run(bot.undo_callback(make_update(query=tap), ctx))
    assert tap.edits == ["↩️ Отменено\n<blockquote><s>кофе — 350 дин</s>\n"
                         "Food · 19.11 · Azat</blockquote>"]
    assert tap.markups == [None]
    assert sheets.read_data_values() == [HEADERS]


def test_draft_card_is_sent_with_coloured_save_and_cancel():
    ctx = _ctx(FakeSheetsClient())
    session = draft.DraftSession(transcript="кофе", items=[{
        "date": "19.11.2026", "category": "Food", "amount": 350.0, "currency": "дин",
        "who": "Azat", "comment": "кофе"}])
    status = FakeMessage()
    run(bot._show_draft(status, ctx, session))
    text, kw = status.edits[0]
    assert text.startswith("📝 <b>Черновик траты</b>\n<blockquote>кофе</blockquote>")
    first, *rest = kw["reply_markup"].inline_keyboard
    assert [(b.text, b.style) for b in first] == [("✅ Сохранить", "success"),
                                                  ("❌ Отмена", "danger")]
    assert all(b.style is None for row in rest for b in row)


def test_draft_cancel_leaves_one_line(monkeypatch):
    ctx = _ctx(FakeSheetsClient())
    session = draft.add_draft(ctx.user_data, draft.DraftSession(items=[{
        "date": "19.11.2026", "category": "Food", "amount": 350.0, "currency": "дин",
        "who": "Azat", "comment": "кофе"}]))
    tap = FakeQuery(f"vx:{session.id}:cancel")
    run(bot.vx_callback(make_update(query=tap), ctx))
    assert tap.edits == ["Черновик отменён"]
    assert tap.markups == [None]


def test_expired_undo_drops_the_button_and_keeps_the_text(monkeypatch):
    sheets = FakeSheetsClient(data_values=[HEADERS])
    ctx = _ctx(sheets)
    saved_at = datetime(2026, 11, 19, 12)
    [[(_, data, _)]] = undo.remember(ctx.user_data, [], saved_at)
    monkeypatch.setattr(bot.clock, "now", lambda: saved_at + undo.UNDO_WINDOW + timedelta(minutes=1))
    tap = FakeQuery(data)
    run(bot.undo_callback(make_update(query=tap), ctx))
    assert tap.answers[-1][0].startswith("Отменить можно только в первые 30 минут")
    assert tap.edits == [("markup", {"reply_markup": None})]


def test_undo_handler_is_registered_before_the_conversation(monkeypatch):
    app = bot.build_application("123:abc")
    group0 = app.handlers[0]
    patterns = [getattr(h, "pattern", None) for h in group0]
    undo_at = next(i for i, p in enumerate(patterns) if p is not None and p.pattern == "^undo:")
    conv_at = next(i for i, h in enumerate(group0) if type(h).__name__ == "ConversationHandler")
    assert undo_at < conv_at
