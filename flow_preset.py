#!/usr/bin/env python3
"""Preset expense flow: pick a ⚡ template and save immediately."""
from __future__ import annotations

import clock

from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import ContextTypes

from conversation_states import CHOOSE_ACTION, CHOOSE_PRESET
from expense import ExpenseRecord, append_expense
import navigation as nav


async def begin_preset_picker(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    defaults: list[dict],
) -> int:
    """Show ⚡ preset buttons. Returns CHOOSE_PRESET or stays on menu if empty."""
    if not defaults:
        await update.message.reply_text(
            "📭 Заготовок нет. Заполни лист Regular_expenses и жми /reloadcats.")
        return CHOOSE_ACTION
    labels = [f"⚡ {t['name']}" for t in defaults]
    kb = [labels[i:i + 2] for i in range(0, len(labels), 2)]
    kb.append(nav.home_row())
    await update.message.reply_text(
        "🧾 Выбери заготовку — сохранится сразу с сегодняшней датой:",
        reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True),
    )
    return CHOOSE_PRESET


async def choose_preset(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    defaults: list[dict],
    sheets,
    date_fmt: str,
    resolve_who,
) -> int:
    """Save a tapped preset or go home. Unknown labels prompt retry."""
    text = update.message.text
    if nav.is_home_text(text):
        return await nav.go_home(update, context)

    if not text.startswith("⚡"):
        await update.message.reply_text("Выбери заготовку с кнопки ⚡ или «To start».")
        return CHOOSE_PRESET

    name = text.replace("⚡", "", 1).strip()
    preset = next((t for t in defaults if t["name"] == name), None)
    if not preset:
        await update.message.reply_text(
            "Не нашёл такую заготовку (список мог обновиться). Выбери снова или /reloadcats.")
        return CHOOSE_PRESET

    who = preset.get("who") or resolve_who(update)
    date_str = clock.now().strftime(date_fmt)
    append_expense(sheets, ExpenseRecord(
        date=date_str,
        category=preset["category"],
        amount=preset["amount"],
        currency=preset["currency"],
        who=who,
        comment=preset["name"],
    ))
    await update.message.reply_text(
        f"✅ Сохранено: {preset['name']} — {preset['amount']:,.2f} "
        f"{preset['currency']} · {date_str}")
    return await nav.go_home(update, context)
