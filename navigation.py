#!/usr/bin/env python3
"""Shared navigation for Telegram conversation flows.

go_home is the single locality for «return to main menu» behaviour.
"""
from __future__ import annotations

from telegram import InlineKeyboardButton, ReplyKeyboardMarkup, Update
from telegram.ext import ContextTypes

from conversation_states import CHOOSE_ACTION

HOME_BUTTON = "🏠 To start"
HOME_LABELS = {HOME_BUTTON, "To start"}
HOME_CALLBACK = "to_start"


def is_home_text(text: str | None) -> bool:
    return (text or "").strip() in HOME_LABELS


def is_home_callback(data: str | None) -> bool:
    return data == HOME_CALLBACK


def home_row() -> list[str]:
    return [HOME_BUTTON]


def home_inline_button() -> InlineKeyboardButton:
    return InlineKeyboardButton(HOME_BUTTON, callback_data=HOME_CALLBACK)


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    kb = [
        ["💰 Add expense", "🧾 Add preset"],
        ["📊 Show statistics", "📈 Dashboard"],
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
        "👋 Hi! What would you like to do?",
        reply_markup=main_menu_keyboard(),
    )


async def go_home(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Clear draft/flow state and return to the main menu."""
    context.user_data.clear()
    await show_main_menu(update, context)
    return CHOOSE_ACTION
