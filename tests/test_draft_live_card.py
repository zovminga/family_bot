"""Draft edit in one live card (issue #102, spec docs/specs/2026-11-bot-look.md, stories 25–29, 35).

Tests check what the person sees: which messages the bot sends, edits and deletes.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest
from telegram import Chat, Message
from telegram.error import BadRequest
from telegram.ext import ApplicationHandlerStop, ExtBot

from family_bot import bot, draft_expense as draft
from family_bot import live_card
from tests.conftest import FakeSheetsClient, bot_context
from tests.known_bugs.fakes import FakeBot, FakeMessage, FakeQuery, make_update


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def tg():
    return FakeBot()


@pytest.fixture
def ctx(tg):
    context = bot_context(sheets=FakeSheetsClient(), currencies=["дин", "€"],
                          allowed_users={111: "Azat", 222: "Lisa"})
    context.bot = tg
    return context


def _draft(ctx, comment="кофе"):
    return draft.add_draft(ctx.user_data, draft.DraftSession(items=[{
        "date": "19.11.2026", "category": "Food", "amount": 350.0, "currency": "дин",
        "who": "Azat", "comment": comment}]))


def _card(tg, session, chat_type="private"):
    """The Draft card as the bot sent it."""
    return FakeMessage(draft.card_text(session), bot=tg, chat_type=chat_type, from_bot=True)


def _tap(ctx, card, session, payload):
    query = FakeQuery(f"vx:{session.id}:{payload}", message=card)
    run(bot.vx_callback(make_update(query=query), ctx))
    return query


def _type(ctx, tg, text, chat_type="private"):
    upd = make_update(text, bot=tg)
    upd.message.chat.type = chat_type
    with pytest.raises(ApplicationHandlerStop):
        run(bot.draft_amount_text(upd, ctx))
    return upd.message


def _labels(kw):
    return [b.text for row in kw["reply_markup"].inline_keyboard for b in row]


# ---------- answers to the card are deleted, the card is edited in place ----------

def test_amount_answer_is_deleted_and_card_edited(ctx, tg):
    session = _draft(ctx)
    card = _card(tg, session)
    _tap(ctx, card, session, "amt:0")
    reply = _type(ctx, tg, "1 200")
    assert reply.deleted
    assert tg.sent == [card]  # no new bot message
    text, kw = card.edits[-1]
    assert "1 200 дин" in text and "Введите" not in text
    assert "✅ Сохранить" in _labels(kw)


def test_bad_amount_is_deleted_and_card_shows_reason_and_same_step(ctx, tg):
    session = _draft(ctx)
    card = _card(tg, session)
    _tap(ctx, card, session, "amt:0")
    reply = _type(ctx, tg, "0")
    assert reply.deleted
    assert tg.sent == [card]
    text, kw = card.edits[-1]
    assert draft.AMOUNT_ERROR in text and "Введите новую сумму" in text
    assert _labels(kw) == ["⬅️ Назад"]
    # The same step: the next good answer is taken.
    _type(ctx, tg, "500")
    assert session.items[0]["amount"] == 500.0


def test_typed_currency_answers_are_deleted(ctx, tg):
    session = _draft(ctx)
    card = _card(tg, session)
    _tap(ctx, card, session, "cur:0:page:0")
    unknown = _type(ctx, tg, "тугрик")
    found = _type(ctx, tg, "лари")
    assert unknown.deleted and found.deleted
    assert session.items[0]["currency"] == "GEL"
    assert tg.sent == [card]


def test_menu_button_during_amount_wait_is_not_deleted(ctx, tg):
    session = _draft(ctx)
    _tap(ctx, _card(tg, session), session, "amt:0")
    upd = make_update("📊 Статистика", bot=tg)
    assert run(bot.draft_amount_text(upd, ctx)) is None
    assert not upd.message.deleted


def test_same_bad_answer_twice_keeps_the_card():
    """Telegram refuses an edit that changes nothing; the card stays as it is."""
    class SameCard(FakeMessage):
        async def edit_text(self, text: str, **kw: Any) -> None:
            raise BadRequest("Message is not modified: specified new message content "
                             "and reply markup are exactly the same")

    card = SameCard("card", from_bot=True)
    assert run(live_card.show(card, "card")) is card


# ---------- a buried card moves to the bottom ----------

def test_buried_card_is_deleted_and_sent_again_at_the_bottom(ctx, tg):
    a = _draft(ctx, "кофе")
    card_a = _card(tg, a)
    _card(tg, _draft(ctx, "хлеб"))  # Draft B came later, below card A
    _tap(ctx, card_a, a, "amt:0")
    assert card_a.deleted and card_a.edits == []
    moved = tg.sent[-1]
    assert moved is not card_a and moved.message_id > card_a.message_id
    assert "кофе" in moved.text and "Введите новую сумму" in moved.text
    assert _labels(moved.kw) == ["⬅️ Назад"]
    # The answer edits the moved card, not the deleted one.
    _type(ctx, tg, "1 200")
    text, kw = moved.edits[-1]
    assert "1 200 дин" in text
    assert "✅ Сохранить" in _labels(kw)


def test_buried_card_moves_with_same_text_on_button_only_step(ctx, tg):
    a = _draft(ctx)
    card_a = _card(tg, a)
    _card(tg, _draft(ctx, "хлеб"))
    _tap(ctx, card_a, a, "who:0")
    moved = tg.sent[-1]
    assert card_a.deleted
    assert moved.text == draft.card_text(a)
    assert _labels(moved.kw) == ["Azat", "Lisa", "⬅️ Назад"]


def test_person_messages_below_the_card_do_not_count(ctx, tg):
    session = _draft(ctx)
    card = _card(tg, session)
    FakeMessage("просто текст", bot=tg)  # the person wrote something, the bot did not
    _tap(ctx, card, session, "amt:0")
    assert not card.deleted and tg.sent == [card]
    assert "Введите новую сумму" in card.edits[-1][0]


def test_deleted_bot_message_below_the_card_does_not_count(ctx, tg):
    session = _draft(ctx)
    card = _card(tg, session)
    other = FakeMessage("вопрос", bot=tg, from_bot=True)
    run(other.delete())
    _tap(ctx, card, session, "amt:0")
    assert not card.deleted


def test_save_on_a_buried_card_stays_in_place(ctx, tg):
    session = _draft(ctx)
    card = _card(tg, session)
    _card(tg, _draft(ctx, "хлеб"))
    query = _tap(ctx, card, session, "save")
    assert not card.deleted
    assert query.edits[-1].startswith("✅ Сохранено")


def test_group_chat_card_is_not_moved_and_answer_kept(ctx, tg):
    session = _draft(ctx)
    card = _card(tg, session, chat_type="group")
    _card(tg, _draft(ctx, "хлеб"), chat_type="group")
    _tap(ctx, card, session, "amt:0")
    assert not card.deleted
    reply = _type(ctx, tg, "1 200", chat_type="group")
    assert not reply.deleted
    assert "1 200 дин" in card.edits[-1][0]


def test_several_drafts_are_edited_each_on_its_own(ctx, tg):
    a, b = _draft(ctx, "кофе"), _draft(ctx, "хлеб")
    card_a, card_b = _card(tg, a), _card(tg, b)
    _tap(ctx, card_b, b, "amt:0")
    _type(ctx, tg, "900")
    _tap(ctx, card_b, b, "large:0")
    assert (a.items[0]["amount"], b.items[0]["amount"]) == (350.0, 900.0)
    assert card_a.edits == [] and not card_a.deleted
    # Card A is now buried under card B: editing it brings it down.
    _tap(ctx, card_a, a, "amt:0")
    _type(ctx, tg, "100")
    assert a.items[0]["amount"] == 100.0 and b.items[0]["amount"] == 900.0
    assert card_a.deleted and "100 дин" in tg.sent[-1].edits[-1][0]


# ---------- voice is deleted after parsing, a receipt photo stays ----------

class _File:
    async def download_as_bytearray(self) -> bytearray:
        return bytearray(b"data")


class _MediaBot(FakeBot):
    async def get_file(self, file_id: str) -> _File:
        return _File()


ITEM = {"date": "19.11.2026", "category": "Food", "amount": 350.0, "currency": "дин",
        "comment": "кофе"}


def _media(kind: str):
    tg = _MediaBot()
    msg = FakeMessage(None, bot=tg)
    msg.voice = SimpleNamespace(file_id="v1") if kind == "voice" else None
    msg.audio = None
    msg.photo = [SimpleNamespace(file_id="p1")] if kind == "photo" else None
    msg.document = None
    upd = SimpleNamespace(message=msg, effective_message=msg, callback_query=None,
                          effective_chat=msg.chat,
                          effective_user=SimpleNamespace(id=111, username="u", first_name="U"))
    ctx = bot_context(sheets=FakeSheetsClient(), currencies=["дин", "€"])
    ctx.bot = tg
    return upd, msg, ctx


def test_voice_is_deleted_after_the_draft_card(monkeypatch):
    monkeypatch.setattr(bot.vx, "transcribe", lambda b: "кофе триста пятьдесят")
    monkeypatch.setattr(bot.vx, "extract_expenses",
                        lambda *a, **kw: {"expenses": [dict(ITEM)]})
    upd, msg, ctx = _media("voice")
    run(bot.voice_handler(upd, ctx))
    status = msg.bot.sent[-1]
    assert status.edits[-1][0].startswith("📝")
    assert msg.deleted


def test_voice_not_understood_is_kept(monkeypatch):
    monkeypatch.setattr(bot.vx, "transcribe", lambda b: "ммм")
    monkeypatch.setattr(bot.vx, "extract_expenses", lambda *a, **kw: {"expenses": []})
    upd, msg, ctx = _media("voice")
    run(bot.voice_handler(upd, ctx))
    assert not msg.deleted


def test_receipt_photo_is_kept(monkeypatch):
    monkeypatch.setattr(bot.px, "extract_receipt",
                        lambda *a, **kw: {"expenses": [dict(ITEM)], "merchant": "Shop"})
    upd, msg, ctx = _media("photo")
    run(bot.photo_handler(upd, ctx))
    assert msg.bot.sent[-1].edits[-1][0].startswith("📝")
    assert not msg.deleted


# ---------- the real bot remembers its own messages ----------

def _message(message_id: int) -> Message:
    return Message(message_id=message_id, date=datetime(2026, 11, 23),
                   chat=Chat(id=111, type="private"))


def test_tracking_bot_remembers_sends_not_edits_and_forgets_deletes(monkeypatch):
    async def fake_send(self, endpoint, data, *a, **kw):
        return _message(data["id"])

    async def fake_delete(self, chat_id, message_id, *a, **kw):
        return True

    monkeypatch.setattr(ExtBot, "_send_message", fake_send)
    monkeypatch.setattr(ExtBot, "delete_message", fake_delete)
    tracking = live_card.TrackingBot("123:abc")
    run(tracking._send_message("sendMessage", {"id": 5}))
    run(tracking._send_message("sendMessage", {"id": 7}))
    run(tracking._send_message("editMessageText", {"id": 5}))
    assert tracking.sent_log.last(111) == 7
    run(tracking.delete_message(111, 7))
    assert tracking.sent_log.last(111) == 5


def test_application_uses_the_tracking_bot():
    app = bot.build_application("123:abc")
    assert isinstance(app.bot, live_card.TrackingBot)
