#!/usr/bin/env python3
"""Tests for navigation helpers and conversation state layout."""
from __future__ import annotations

import conversation_states as states
import navigation as nav


def test_is_home_text():
    assert nav.is_home_text("🏠 To start")
    assert nav.is_home_text("To start")
    assert not nav.is_home_text("💰 Add expense")
    assert not nav.is_home_text(None)


def test_is_home_callback():
    assert nav.is_home_callback("to_start")
    assert not nav.is_home_callback("skip")


def test_home_row():
    assert nav.home_row() == ["🏠 To start"]


def test_main_menu_keyboard_has_four_actions():
    markup = nav.main_menu_keyboard()
    labels = {btn.text for row in markup.keyboard for btn in row}
    assert "💰 Add expense" in labels
    assert "🧾 Add default expense" in labels
    assert "📊 Show statistics" in labels
    assert "📈 Dashboard" in labels
    assert "🏠 To start" in labels


def test_conversation_states_are_unique_and_count_16():
    values = [
        states.CHOOSE_ACTION,
        states.CHOOSE_CAT,
        states.TYPING_AMT,
        states.CHOOSE_CUR,
        states.TYPING_CMNT,
        states.CHOOSE_DT,
        states.TYPING_DT,
        states.CHOOSE_PRESET,
        states.STAT_CAT,
        states.STAT_TYPE,
        states.STAT_DATE_FROM,
        states.STAT_DATE_TO,
        states.STAT_MONTH,
        states.STAT_GROUP_CURRENCY,
        states.STAT_CONVERT_CURRENCY,
        states.STAT_SHOW_DETAILS,
    ]
    assert len(values) == 16
    assert len(set(values)) == 16
    assert states.CHOOSE_PRESET != states.CHOOSE_CAT


def test_begin_preset_empty_stays_on_menu():
    import asyncio
    from types import SimpleNamespace

    import flow_preset

    class Msg:
        async def reply_text(self, *a, **k):
            self.text = a[0] if a else None

    update = SimpleNamespace(message=Msg())
    context = SimpleNamespace(user_data={})
    result = asyncio.get_event_loop().run_until_complete(
        flow_preset.begin_preset_picker(update, context, [])
    )
    assert result == states.CHOOSE_ACTION
    assert "Заготовок нет" in update.message.text


def test_begin_preset_with_items_enters_preset_state():
    import asyncio
    from types import SimpleNamespace

    import flow_preset

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
    assert result == states.CHOOSE_PRESET
    assert "⚡ Rent" in {
        btn.text for row in update.message.markup.keyboard for btn in row
    }
