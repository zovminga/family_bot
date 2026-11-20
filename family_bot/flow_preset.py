#!/usr/bin/env python3
"""Preset expense flow: pick a ⚡ template and save it.

A Preset with an amount saves at once. A Preset without one (``amount=None``)
first asks the amount, checked like manual input (`amount_input`).
"""
from __future__ import annotations

import asyncio
from typing import cast

from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import ContextTypes

from family_bot import amount_input, clock
from family_bot.currency import format_amount
from family_bot.conversation_states import State
from family_bot.expense import ExpenseRecord, append_expense
from family_bot import navigation as nav

# user_data key: the Preset waiting for its amount.
KEY_PRESET = "preset"


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
        "🧾 Выбери заготовку — сохранится с сегодняшней датой:",
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

    if preset["amount"] is None:
        context.user_data.update({KEY_PRESET: preset})
        await update.message.reply_text(
            f"💵 Введите сумму для «{preset['name']}» ({preset['currency']}):",
            reply_markup=ReplyKeyboardMarkup([nav.home_row()], resize_keyboard=True))
        return State.TYPING_PRESET_AMT
    return await _save(update, context, preset, preset["amount"], preset["currency"],
                       sheets=sheets, date_fmt=date_fmt, resolve_who=resolve_who)


async def type_preset_amount(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    sheets,
    date_fmt: str,
    resolve_who,
) -> int:
    """Save the waiting Preset with the typed amount; a typed Currency wins."""
    # Set by choose_preset, the only way into this state.
    preset: dict = cast(dict, context.user_data)[KEY_PRESET]
    parsed = amount_input.parse_amount_text(update.message.text or "")
    if parsed is None:
        await update.message.reply_text(amount_input.RETRY_TEXT)
        return State.TYPING_PRESET_AMT
    amount, typed = parsed
    cur = typed.symbol if typed is not None else preset["currency"]
    return await _save(update, context, preset, amount, cur,
                       sheets=sheets, date_fmt=date_fmt, resolve_who=resolve_who)


async def _save(update: Update, context: ContextTypes.DEFAULT_TYPE, preset: dict,
                amount: float, cur: str, *, sheets, date_fmt: str, resolve_who) -> int:
    """Write the Preset as today's Expense, its name as the comment; go home."""
    who = preset.get("who") or resolve_who(update)
    date_str = clock.now().strftime(date_fmt)
    await asyncio.to_thread(append_expense, sheets, ExpenseRecord(
        date=date_str,
        category=preset["category"],
        amount=amount,
        currency=cur,
        who=who,
        comment=preset["name"],
    ))
    await update.message.reply_text(
        f"✅ Сохранено: {preset['name']} — {format_amount(amount, cur)} · {date_str}")
    return await nav.go_home(update, context)
