#!/usr/bin/env python3
"""Shared navigation for Telegram conversation flows.

go_home is the single locality for «return to main menu» behaviour.
"""
from __future__ import annotations

import re

from telegram import InlineKeyboardButton, ReplyKeyboardMarkup, Update
from telegram.ext import BaseHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters

from family_bot.conversation_states import State
from family_bot.draft_expense import KEY_DRAFTS
from family_bot.undo import KEY_LAST_SAVE

HOME_BUTTON = "🏠 В начало"
# Reply keyboards stay on the phone until replaced, so labels from before the
# Russian UI still work.
HOME_LABELS = {HOME_BUTTON, "В начало", "🏠 To start", "To start"}
HOME_CALLBACK = "to_start"

# Home button text, outer spaces ignored.
HOME_TEXT = filters.Regex(
    r"^\s*(?:" + "|".join(re.escape(label) for label in HOME_LABELS) + r")\s*$")
# Text a conversation state reads: not a command, not the home button.
INPUT_TEXT = filters.TEXT & ~filters.COMMAND & ~HOME_TEXT
# Callback data a state reads: anything except the home button.
NOT_HOME_CALLBACK = f"^(?!{HOME_CALLBACK}$)"


# Main-menu buttons; bot.choose_action matches on these.
ADD_EXPENSE = "💰 Добавить трату"
ADD_PRESET = "🧾 Заготовка"
THIS_MONTH = "📅 Этот месяц"
SHOW_STATS = "📊 Статистика"
DASHBOARD = "📈 Дашборд"

# Every text that means a menu button -> that button. English labels: reply
# keyboards sent before the Russian UI stay on phones.
MENU_ACTIONS: dict[str, str] = {
    ADD_EXPENSE: ADD_EXPENSE, "💰 Add expense": ADD_EXPENSE, "Add expense": ADD_EXPENSE,
    ADD_PRESET: ADD_PRESET, "🧾 Add preset": ADD_PRESET, "Add preset": ADD_PRESET,
    "🧾 Add default expense": ADD_PRESET, "Add default expense": ADD_PRESET,
    THIS_MONTH: THIS_MONTH,
    SHOW_STATS: SHOW_STATS, "📊 Show statistics": SHOW_STATS, "Show statistics": SHOW_STATS,
    DASHBOARD: DASHBOARD, "📈 Dashboard": DASHBOARD, "Dashboard": DASHBOARD,
}
# A menu button tap: a conversation entry point, so the menu works with no
# conversation state (right after a restart, when memory is empty).
MENU_TEXT = filters.Text(list(MENU_ACTIONS))


def home_row() -> list[str]:
    return [HOME_BUTTON]


def home_inline_button() -> InlineKeyboardButton:
    return InlineKeyboardButton(HOME_BUTTON, callback_data=HOME_CALLBACK)


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    kb = [
        [ADD_EXPENSE, ADD_PRESET],
        [THIS_MONTH, SHOW_STATS],
        [DASHBOARD],
        home_row(),
    ]
    return ReplyKeyboardMarkup(kb, resize_keyboard=True)


async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send the main action keyboard (does not clear user_data)."""
    if update.message:
        target = update.message
    elif update.callback_query and update.callback_query.message:
        target = update.callback_query.message
    else:
        return
    await target.reply_text(
        "👋 Привет! Что делаем?",
        reply_markup=main_menu_keyboard(),
    )


async def go_home(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Clear flow state and return to the main menu; open Drafts and the last save stay."""
    kept = {k: v for k, v in context.user_data.items()
            if k in (KEY_DRAFTS, KEY_LAST_SAVE) and v}
    context.user_data.clear()
    context.user_data.update(kept)
    await show_main_menu(update, context)
    return State.CHOOSE_ACTION


async def on_home(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Home button (reply or inline) from any conversation state."""
    if update.callback_query:
        await update.callback_query.answer()
    return await go_home(update, context)


def home_fallbacks() -> list[BaseHandler]:
    """ConversationHandler fallbacks: the one place the home button is handled."""
    return [
        MessageHandler(HOME_TEXT, on_home),
        CallbackQueryHandler(on_home, pattern=f"^{HOME_CALLBACK}$"),
    ]
