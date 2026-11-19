"""After a bot restart (Render deploy wipes memory): menu, old Draft card, old undo button."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import pytest
from telegram import CallbackQuery, Chat, Message, Update, User
from telegram.ext import ConversationHandler

from family_bot import bot
from family_bot import draft_expense as draft
from family_bot import navigation as nav
from family_bot import undo
from family_bot.conversation_states import State
from tests.conftest import FakeSheetsClient, bot_context
from tests.known_bugs.fakes import FakeQuery, make_update

USER = User(id=111, first_name="A", is_bot=False)


def run(coro: Any) -> Any:
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def fresh_conversation() -> ConversationHandler:
    """The conversation as right after a restart: no state for any User."""
    app = bot.build_application("123:abc")
    [conv] = [h for h in app.handlers[0] if isinstance(h, ConversationHandler)]
    return conv


def text_update(text: str) -> Update:
    msg = Message(message_id=5, date=datetime.now(timezone.utc),
                  chat=Chat(id=111, type="private"), from_user=USER, text=text)
    return Update(update_id=1, message=msg)


def callback_update(data: str) -> Update:
    msg = Message(message_id=5, date=datetime.now(timezone.utc),
                  chat=Chat(id=111, type="private"), from_user=USER, text="card")
    return Update(update_id=1, callback_query=CallbackQuery(
        id="1", from_user=USER, chat_instance="c", data=data, message=msg))


# Each menu label (new and old English) and the flow method it must reach.
ROUTES = [
    (nav.ADD_EXPENSE, "manual"), ("💰 Add expense", "manual"), ("Add expense", "manual"),
    (nav.ADD_PRESET, "preset"), ("🧾 Add preset", "preset"), ("Add preset", "preset"),
    ("🧾 Add default expense", "preset"), ("Add default expense", "preset"),
    (nav.THIS_MONTH, "this_month"),
    (nav.SHOW_STATS, "stats"), ("📊 Show statistics", "stats"), ("Show statistics", "stats"),
    (nav.DASHBOARD, "dashboard"), ("📈 Dashboard", "dashboard"), ("Dashboard", "dashboard"),
]


@pytest.mark.parametrize("label,flow", ROUTES)
def test_menu_button_after_restart_starts_its_flow(
        label: str, flow: str, monkeypatch: pytest.MonkeyPatch) -> None:
    conv = fresh_conversation()
    checked = conv.check_update(text_update(label))
    assert checked is not None, "a fresh conversation must accept the menu button"
    handler = checked[2]

    called: list[str] = []

    def mark(name: str) -> Any:
        async def fake(*args: Any, **kw: Any) -> int:
            called.append(name)
            return State.CHOOSE_ACTION
        return fake

    monkeypatch.setattr(bot.MANUAL, "begin", mark("manual"))
    monkeypatch.setattr(bot.STATS, "begin", mark("stats"))
    monkeypatch.setattr(bot.STATS, "this_month", mark("this_month"))
    monkeypatch.setattr(bot.flow_preset, "begin_preset_picker", mark("preset"))
    monkeypatch.setattr(bot, "send_dashboard", mark("dashboard"))

    ctx = bot_context()  # fresh user_data, Config reloaded by post_init
    run(handler.callback(make_update(label), ctx))
    assert called == [flow]


@pytest.mark.parametrize("update", [text_update(nav.HOME_BUTTON), callback_update(nav.HOME_CALLBACK)],
                         ids=["reply", "inline"])
def test_home_button_after_restart_shows_menu(update: Update) -> None:
    assert fresh_conversation().check_update(update) is not None


def test_other_text_after_restart_still_not_a_conversation() -> None:
    assert fresh_conversation().check_update(text_update("hello")) is None


def test_menu_label_inside_a_flow_goes_to_that_state() -> None:
    """Entry points only start a conversation; a running flow keeps its own reading."""
    conv = fresh_conversation()
    update = text_update(nav.DASHBOARD)
    key = conv._get_key(update)
    conv._conversations[key] = State.TYPING_CMNT
    checked = conv.check_update(update)
    assert checked is not None
    assert checked[2] in conv.states[State.TYPING_CMNT]


def test_old_draft_card_after_restart_says_draft_is_stale() -> None:
    ctx = bot_context(sheets=FakeSheetsClient())  # no Drafts: memory was wiped
    tap = FakeQuery("vx:ab12:save")
    run(bot.vx_callback(make_update(query=tap), ctx))
    assert tap.answers == [(draft.STALE_TEXT,)]
    assert tap.edits == [("markup", {"reply_markup": None})]  # card text stays, buttons go


def test_old_undo_button_after_restart_explains_in_an_alert() -> None:
    ctx = bot_context(sheets=FakeSheetsClient())  # no last save: memory was wiped
    tap = FakeQuery(undo.CALLBACK_PREFIX + "deadbeef")
    run(bot.undo_callback(make_update(query=tap), ctx))
    assert tap.edits == []
    [(text,)] = tap.answers
    assert "пока бот не перезапускался" in text
