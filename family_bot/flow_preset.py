#!/usr/bin/env python3
"""Preset expense flow: pick a ⚡ template and save it.

A Preset with an amount saves at once. A Preset without one (``amount=None``)
first asks the amount on a live card (``live_card``), checked like manual input
(`amount_input`): the typed answers are deleted, a wrong one shows the reason
on the card. Either save ends as «✅ Сохранено» with «↩️ Отменить»; the card
becomes that line.
"""
from __future__ import annotations

import asyncio
from typing import cast

from telegram import Message, ReplyKeyboardMarkup, Update
from telegram.ext import ContextTypes

from family_bot import amount_input, clock, live_card, look, undo
from family_bot.conversation_states import State
from family_bot.draft_expense import AMOUNT_ERROR
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
            "📭 Заготовок нет. Заполни лист Regular_expenses и отправь /reloadcats.")
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
        cast(dict, context.user_data)[live_card.KEY_CARD] = await update.message.reply_text(
            _amount_card(preset), reply_markup=nav.inline_markup([], home=True))
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
    user_data = cast(dict, context.user_data)
    message = cast(Message, update.message)  # this state reads text messages only
    preset: dict = user_data[KEY_PRESET]
    await live_card.drop_reply(message)
    parsed = amount_input.parse_amount_text(message.text or "")
    if parsed is None:
        card = user_data.get(live_card.KEY_CARD)
        text = _amount_card(preset, AMOUNT_ERROR)
        markup = nav.inline_markup([], home=True)
        user_data[live_card.KEY_CARD] = (
            await live_card.show(card, text, markup) if card is not None
            else await message.reply_text(text, reply_markup=markup))
        return State.TYPING_PRESET_AMT
    amount, typed = parsed
    cur = typed.symbol if typed is not None else preset["currency"]
    return await _save(update, context, preset, amount, cur,
                       sheets=sheets, date_fmt=date_fmt, resolve_who=resolve_who)


def _amount_card(preset: dict, error: str = "") -> str:
    """Card asking the amount of a Preset without one (HTML)."""
    lines = [f"⚡ {look.bold(preset['name'])}",
             look.field("Категория", preset["category"])]
    if preset.get("who"):
        lines.append(look.field("Кто", preset["who"]))
    lines.append("")
    if error:
        lines.append(look.escape(error))
    lines.append(look.escape(f"💵 Введи сумму ({preset['currency']}):"))
    return "\n".join(lines)


async def _save(update: Update, context: ContextTypes.DEFAULT_TYPE, preset: dict,
                amount: float, cur: str, *, sheets, date_fmt: str, resolve_who) -> int:
    """Write the Preset as today's Expense, its name as the comment; go home.

    The amount card (if any) becomes «✅ Сохранено» with «↩️ Отменить».
    """
    who = preset.get("who") or resolve_who(update)
    record = ExpenseRecord(
        date=clock.now().strftime(date_fmt),
        category=preset["category"],
        amount=amount,
        currency=cur,
        who=who,
        comment=preset["name"],
        large=False,  # a Preset is routine even above the threshold
    )
    await asyncio.to_thread(append_expense, sheets, record)
    text = undo.saved_text([record])
    user_data = cast(dict, context.user_data)
    markup = nav.inline_markup(undo.remember(user_data, [record], clock.now()))
    card = user_data.pop(live_card.KEY_CARD, None)  # a result now: go_home keeps it
    if card is None:
        await update.message.reply_text(text, reply_markup=markup)
    else:
        await live_card.show(card, text, markup)
    # The ⚡ keyboard replaced the menu: send the menu again.
    return await nav.go_home(update, context)
