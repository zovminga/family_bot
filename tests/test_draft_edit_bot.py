"""Draft card «✏️ Сумма» / «👤 Кто» wiring in the bot (issue #31)."""
from __future__ import annotations

import asyncio

import pytest
from telegram.ext import ApplicationHandlerStop

from family_bot import bot, draft_expense as draft
from family_bot import navigation as nav
from tests.conftest import FakeSheetsClient, bot_context
from tests.known_bugs.fakes import FakeQuery, make_update


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def ctx():
    return bot_context(sheets=FakeSheetsClient(), currencies=["дин", "€"],
                       allowed_users={111: "Azat", 222: "Lisa"})


def _open_draft(ctx):
    return draft.add_draft(ctx.user_data, draft.DraftSession(items=[{
        "date": "19.11.2026", "category": "Food", "amount": 350.0, "currency": "дин",
        "who": "Azat", "comment": "кофе"}]))


def _tap(ctx, session, payload):
    query = FakeQuery(f"vx:{session.id}:{payload}")
    run(bot.vx_callback(make_update(query=query), ctx))
    return query


def test_typed_amount_updates_the_card(ctx):
    session = _open_draft(ctx)
    query = _tap(ctx, session, "amt:0")
    assert "Введите новую сумму" in query.edits[0]
    with pytest.raises(ApplicationHandlerStop):
        run(bot.draft_amount_text(make_update("1 200 €"), ctx))
    assert (session.items[0]["amount"], session.items[0]["currency"]) == (1200.0, "€")
    card, kw = query.message.edits[-1]
    assert "1 200 €" in card
    assert draft.KEY_AMOUNT_EDIT not in ctx.user_data


def test_bad_amount_is_asked_again(ctx):
    session = _open_draft(ctx)
    _tap(ctx, session, "amt:0")
    upd = make_update("0")
    with pytest.raises(ApplicationHandlerStop):
        run(bot.draft_amount_text(upd, ctx))
    assert "больше нуля" in upd.message.replies[0][0]
    assert session.items[0]["amount"] == 350.0
    assert draft.KEY_AMOUNT_EDIT in ctx.user_data


def test_text_without_pending_edit_passes_through(ctx):
    _open_draft(ctx)
    assert run(bot.draft_amount_text(make_update("кофе 350"), ctx)) is None


def test_home_button_drops_pending_edit_and_passes_through(ctx):
    session = _open_draft(ctx)
    _tap(ctx, session, "amt:0")
    assert run(bot.draft_amount_text(make_update(nav.HOME_BUTTON), ctx)) is None
    assert draft.KEY_AMOUNT_EDIT not in ctx.user_data


def test_other_card_button_drops_pending_edit(ctx):
    session = _open_draft(ctx)
    _tap(ctx, session, "amt:0")
    _tap(ctx, session, "menu")
    assert draft.KEY_AMOUNT_EDIT not in ctx.user_data


def test_pending_edit_on_saved_draft_passes_through(ctx):
    session = _open_draft(ctx)
    _tap(ctx, session, "amt:0")
    session.closed = True
    assert run(bot.draft_amount_text(make_update("500"), ctx)) is None
    assert session.items[0]["amount"] == 350.0


def test_who_button_lists_allowlist_spenders(ctx):
    session = _open_draft(ctx)
    query = _tap(ctx, session, "who:0")
    _, kw = query.edits[0]
    labels = [b.text for row in kw["reply_markup"].inline_keyboard for b in row]
    assert labels == ["Azat", "Lisa", "⬅️ Назад"]
    _tap(ctx, session, "setwho:0:1")
    assert session.items[0]["who"] == "Lisa"


def test_amount_edit_runs_after_allowlist_and_before_conversation():
    app = bot.build_application("123:abc")
    groups = app.handlers
    assert bot.EDITED_MESSAGES in groups[min(groups)]
    allowlist = next(g for g, hs in groups.items()
                     if any(getattr(h, "callback", None) is bot.enforce_allowlist for h in hs))
    amount = next(g for g, hs in groups.items()
                  if any(getattr(h, "callback", None) is bot.draft_amount_text for h in hs))
    assert min(groups) < allowlist < amount < 0
