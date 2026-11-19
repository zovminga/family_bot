"""ConversationHandler wiring: every state has a handler, home is handled once in fallbacks."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from enum import IntEnum
from typing import Any

import pytest
from telegram import CallbackQuery, Chat, Message, Update, User
from telegram.ext import ConversationHandler

from family_bot import bot
from family_bot import navigation as nav
from family_bot.conversation_states import State
from family_bot.draft_expense import KEY_DRAFTS
from tests.known_bugs.fakes import FakeQuery, make_update

USER = User(id=111, first_name="A", is_bot=False)


def run(coro: Any) -> Any:
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def conversation() -> ConversationHandler:
    app = bot.build_application("123:abc")
    convs = [h for h in app.handlers[0] if isinstance(h, ConversationHandler)]
    assert len(convs) == 1
    return convs[0]


def text_update(text: str) -> Update:
    msg = Message(message_id=5, date=datetime.now(timezone.utc),
                  chat=Chat(id=111, type="private"), from_user=USER, text=text)
    return Update(update_id=1, message=msg)


def callback_update(data: str) -> Update:
    return Update(update_id=1, callback_query=CallbackQuery(
        id="1", from_user=USER, chat_instance="c", data=data))


def matches(handlers: list[Any], update: Update) -> bool:
    return any(h.check_update(update) not in (None, False) for h in handlers)


def test_states_are_an_int_enum_without_duplicates() -> None:
    assert issubclass(State, IntEnum)
    assert len({s.value for s in State}) == len(State) == 16


def test_every_state_has_a_handler() -> None:
    assert set(conversation().states) == set(State)


@pytest.mark.parametrize("update", [
    text_update("🏠 To start"), text_update("To start"), text_update(" To start "),
    callback_update("to_start"),
], ids=["button", "plain", "spaces", "inline"])
def test_home_skips_every_state_and_hits_fallback(update: Update) -> None:
    conv = conversation()
    for state, handlers in conv.states.items():
        assert not matches(handlers, update), state
    assert matches(conv.fallbacks, update)


def test_other_text_still_reaches_text_states() -> None:
    conv = conversation()
    update = text_update("350")
    for state in State:
        if state is State.CHOOSE_DT:
            continue  # buttons only
        assert matches(conv.states[state], update), state


def test_home_callback_keeps_drafts_and_clears_flow() -> None:
    ctx_data: dict[str, Any] = {"cat": "Food", KEY_DRAFTS: {"1": object()}}
    ctx = type("Ctx", (), {"user_data": ctx_data})()
    query = FakeQuery("to_start")
    assert run(nav.on_home(make_update(query=query), ctx)) == State.CHOOSE_ACTION
    assert set(ctx_data) == {KEY_DRAFTS}
