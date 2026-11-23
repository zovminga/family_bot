#!/usr/bin/env python3
"""Tests for navigation helpers and conversation state layout."""
from __future__ import annotations

from family_bot import conversation_states as states
from family_bot import navigation as nav


def test_home_row():
    assert nav.home_row() == ["🏠 В начало"]


def test_main_menu_is_two_rows_without_home():
    markup = nav.main_menu_keyboard()
    rows = [[btn.text for btn in row] for row in markup.keyboard]
    assert rows == [["💰 Добавить трату", "🧾 Заготовка"],
                    ["📅 Этот месяц", "📊 Статистика", "📈 Дашборд"]]


def test_old_keyboard_labels_still_work():
    """A phone keeps the old keyboard until the bot sends the new one."""
    for label in ("🏠 В начало", "В начало", "🏠 To start"):
        assert nav.HOME_TEXT.pattern.match(label)
        assert nav.is_menu_text(label)
    for label in ("💰 Add expense", "📊 Show statistics", "📈 Dashboard"):
        assert nav.MENU_ACTIONS[label] in nav.MENU_LABELS
        assert label in nav.MENU_TEXT.strings


def test_begin_preset_empty_stays_on_menu():
    import asyncio
    from types import SimpleNamespace

    from family_bot import flow_preset

    class Msg:
        async def reply_text(self, *a, **k):
            self.text = a[0] if a else None

    update = SimpleNamespace(message=Msg())
    context = SimpleNamespace(user_data={})
    result = asyncio.get_event_loop().run_until_complete(
        flow_preset.begin_preset_picker(update, context, [])
    )
    assert result == states.State.CHOOSE_ACTION
    assert "Заготовок нет" in update.message.text


def test_begin_preset_with_items_enters_preset_state():
    import asyncio
    from types import SimpleNamespace

    from family_bot import flow_preset

    class Msg:
        def __init__(self):
            self.markup = None

        async def reply_text(self, text, reply_markup=None):
            self.text = text
            self.markup = reply_markup

    update = SimpleNamespace(message=Msg())
    context = SimpleNamespace(user_data={})
    defaults = [{"name": "Rent", "amount": 1, "currency": "€",
                 "category": "Rent", "who": "Both"}]
    result = asyncio.get_event_loop().run_until_complete(
        flow_preset.begin_preset_picker(update, context, defaults)
    )
    assert result == states.State.CHOOSE_PRESET
    assert "⚡ Rent" in {
        btn.text for row in update.message.markup.keyboard for btn in row
    }
