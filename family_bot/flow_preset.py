#!/usr/bin/env python3
"""Preset expense flow: pick a ⚡ template and save immediately."""
from __future__ import annotations

import asyncio

from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import ContextTypes

from family_bot import clock
from family_bot.currency import format_amount
from family_bot.conversation_states import State
from family_bot.expense import ExpenseRecord, append_expense
from family_bot import navigation as nav


async def begin_preset_picker(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    presets: list[dict],
) -> int:
    """Show ⚡ preset buttons. Returns CHOOSE_PRESET or stays on menu if empty."""
    if not presets:
        await update.message.reply_text(
            "📭 Заготовок нет. Заполни лист Regular_expenses и жми /reloadcats.")
        return State.CHOOSE_ACTION
    labels = [f"⚡ {t['name']}" for t in presets]
    kb = [labels[i:i + 2] for i in range(0, len(labels), 2)]
    kb.append(nav.home_row())
    await update.message.reply_text(
        "🧾 Выбери заготовку — сохранится сразу с сегодняшней датой:",
        reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True),
    )
    return State.CHOOSE_PRESET


async def choose_preset(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    presets: list[dict],
    sheets,
    date_fmt: str,
    resolve_who,
) -> int:
    """Save a tapped preset or go home. Unknown labels prompt retry."""
    text = update.message.text
    if not text.startswith("⚡"):
        await update.message.reply_text(f"Выбери заготовку с кнопки ⚡ или «{nav.HOME_BUTTON}».")
        return State.CHOOSE_PRESET

    name = text.replace("⚡", "", 1).strip()
    preset = next((t for t in presets if t["name"] == name), None)
    if not preset:
        await update.message.reply_text(
            "Не нашёл такую заготовку (список мог обновиться). Выбери снова или /reloadcats.")
        return State.CHOOSE_PRESET

    who = preset.get("who") or resolve_who(update)
    date_str = clock.now().strftime(date_fmt)
    await asyncio.to_thread(append_expense, sheets, ExpenseRecord(
        date=date_str,
        category=preset["category"],
        amount=preset["amount"],
        currency=preset["currency"],
        who=who,
        comment=preset["name"],
        large=False,  # a Preset is routine even above the threshold
    ))
    await update.message.reply_text(
        f"✅ Сохранено: {preset['name']} — "
        f"{format_amount(preset['amount'], preset['currency'])} · {date_str}")
    return await nav.go_home(update, context)
