"""Payment reminder in Telegram: the job on the 5th and 20th and its buttons.

Logic lives in ``payment_reminder``; this module sends, edits and answers.
Like ``monthly_summary``, the job lives in the ``JobQueue`` (process memory):
a reminder whose hour passes while the bot is down is skipped, not sent late.

«⚡ Внести» on a payment without an amount asks for it with ``ForceReply``: in
a group with privacy mode on the bot only sees replies to its own messages.
``AMOUNT_REPLY`` takes such a reply (group -1, before the Draft amount text).
"""
from __future__ import annotations

import asyncio
from datetime import time
from typing import Callable, cast

from telegram import ForceReply, InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.ext import (
    ApplicationHandlerStop, CallbackContext, ContextTypes, JobQueue, MessageHandler, filters,
)

from family_bot import amount_input
from family_bot import clock
from family_bot import config
from family_bot import errors
from family_bot import payment_reminder as pr
from family_bot import undo
from family_bot.currency import format_amount
from family_bot.draft_expense import ButtonGrid
from family_bot.expense import append_expense
from family_bot.monthly_summary import job_tz
from family_bot.sheet_data import DATE_FMT, MONTH_FMT, Row

DAYS_OF_MONTH = (5, 20)
JOB_NAME = "payment_reminder"

# Reads (Presets, Data rows) from the sheet; blocking, called in a thread.
LoadInputs = Callable[[], tuple[list[dict], list[Row]]]


def _markup(grid: ButtonGrid) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data=cb) for label, cb in row] for row in grid])


async def send_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Job callback: send this month's due payments to the job's chat.

    No due payment → no message. A failure is logged and the bot keeps running.

    Parameters
    ----------
    context : ContextTypes.DEFAULT_TYPE
        Job context; ``context.job.data`` is the ``LoadInputs`` source.
    """
    job = context.job
    assert job is not None and job.chat_id is not None  # set by schedule()
    load_inputs: LoadInputs = job.data  # type: ignore[assignment]
    try:
        presets, rows = await asyncio.to_thread(load_inputs)
        month = clock.now().strftime(MONTH_FMT)
        due = pr.due_payments(presets, rows, month)
        if not due:
            return
        reminder = pr.new_reminder(due, month)
        sent = await context.bot.send_message(
            job.chat_id, pr.message_text(reminder), reply_markup=_markup(pr.buttons(reminder)))
        reminder.chat_id, reminder.message_id = sent.chat_id, sent.message_id
        pr.add(context.bot_data, reminder)
    except Exception as e:  # noqa: BLE001 - a failed reminder must not stop the bot
        errors.log_exception("Payment reminder", e)


def schedule(job_queue: JobQueue[CallbackContext], chat_id: int, hour: int,
             load_inputs: LoadInputs) -> None:
    """
    Run ``send_reminder`` on the 5th and 20th at ``hour`` family time.

    Parameters
    ----------
    job_queue : JobQueue
        The Application's job queue.
    chat_id : int
        Telegram chat for the reminder (``DASHBOARD_CHAT_ID``).
    hour : int
        Hour 0-23 in the family time zone (``TZ_NAME``).
    load_inputs : LoadInputs
        Reads Presets and Data rows; blocking, called in a thread.
    """
    for day in DAYS_OF_MONTH:
        job_queue.run_monthly(
            send_reminder,
            when=time(hour, tzinfo=job_tz()),
            day=day,
            data=load_inputs,
            name=f"{JOB_NAME}_{day}",
            chat_id=chat_id,
        )


async def _redraw(context: ContextTypes.DEFAULT_TYPE, reminder: pr.Reminder) -> None:
    """Show the reminder's open lines; drop it from memory once all are closed."""
    grid = pr.buttons(reminder)
    await context.bot.edit_message_text(
        pr.message_text(reminder), chat_id=reminder.chat_id, message_id=reminder.message_id,
        reply_markup=_markup(grid) if grid else None)
    pr.forget_if_closed(context.bot_data, reminder)


async def _save(context: ContextTypes.DEFAULT_TYPE, reply_to: Message, reminder: pr.Reminder,
                index: int, amount: float, currency: str) -> None:
    """Write the payment, redraw the reminder, give the presser «✅ Сохранено» + undo.

    The line is closed before the write so a second tap cannot save twice; a
    failed write reopens it.
    """
    payment = pr.take(reminder, index)
    if payment is None:
        await reply_to.reply_text(pr.CLOSED_TEXT)
        return
    preset = payment.preset
    today = clock.now().strftime(DATE_FMT)
    record = pr.record(preset, amount, currency, today)
    try:
        await asyncio.to_thread(append_expense, context.bot_data[config.KEY].sheets, record)
    except Exception as e:  # noqa: BLE001 - tell the User, keep the line
        pr.put_back(reminder, index)
        errors.log_exception("Payment reminder save", e)
        await reply_to.reply_text(errors.SAVE_FAILED)
        return
    undo_grid = undo.remember(cast(dict, context.user_data), [record], clock.now())
    await _redraw(context, reminder)
    await reply_to.reply_text(
        f"✅ Сохранено: {preset['name']} — {format_amount(amount, currency)} · {today}",
        reply_markup=_markup(undo_grid))


async def _ask_amount(context: ContextTypes.DEFAULT_TYPE, reply_to: Message,
                      reminder: pr.Reminder, index: int, user_id: int,
                      prefix: str = "") -> None:
    """Send the amount prompt as a ForceReply and remember who must answer it."""
    preset = reminder.payments[index].preset
    text = prefix + pr.PROMPT_TEXT.format(name=preset["name"], currency=preset["currency"])
    prompt = await reply_to.reply_text(text, reply_markup=ForceReply())
    pr.prompts(context.bot_data)[(prompt.chat_id, prompt.message_id)] = pr.AmountPrompt(
        reminder_id=reminder.id, index=index, user_id=user_id)


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """«⚡ Внести» / «✅ Уже внесено» under a reminder (callback data ``pr:``)."""
    query = update.callback_query
    assert query is not None and query.message is not None  # pattern ^pr:
    parsed = pr.parse_callback(query.data or "")
    reminder = pr.get(context.bot_data, parsed[0]) if parsed else None
    if parsed is None or reminder is None:
        # Keep the text so the family sees what was due; drop dead buttons.
        await query.answer(pr.STALE_TEXT, show_alert=True)
        await query.edit_message_reply_markup(reply_markup=None)
        return
    _, index, action = parsed
    if index not in reminder.open:
        await query.answer(pr.CLOSED_TEXT)
        return
    await query.answer()
    message: Message = query.message  # type: ignore[assignment]
    if action == pr.ACTION_ALREADY:
        pr.take(reminder, index)
        await _redraw(context, reminder)
        return
    preset = reminder.payments[index].preset
    if preset["amount"] is None:
        user = update.effective_user
        assert user is not None  # the Allowlist gate passed
        await _ask_amount(context, message, reminder, index, user.id)
        return
    await _save(context, message, reminder, index, preset["amount"], preset["currency"])


class _ReplyToPrompt(filters.MessageFilter):
    """A message that replies to the bot's amount prompt."""

    def filter(self, message: Message) -> bool:
        reply = message.reply_to_message
        return bool(reply is not None and reply.from_user is not None and reply.from_user.is_bot
                    and reply.text and pr.PROMPT_MARK in reply.text)


REPLY_TO_PROMPT = _ReplyToPrompt()


async def on_amount_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """The presser's amount for a payment without one; checks as in manual input."""
    message = update.message
    assert message is not None and message.reply_to_message is not None  # REPLY_TO_PROMPT
    prompt_msg = message.reply_to_message
    waiting = pr.prompts(context.bot_data)
    key = (prompt_msg.chat_id, prompt_msg.message_id)
    prompt = waiting.get(key)
    reminder = pr.get(context.bot_data, prompt.reminder_id) if prompt else None
    if prompt is None or reminder is None:
        await message.reply_text(pr.STALE_TEXT)
        raise ApplicationHandlerStop
    user = update.effective_user
    assert user is not None  # the Allowlist gate passed
    if user.id != prompt.user_id:
        await message.reply_text(pr.NOT_YOURS_TEXT)
        raise ApplicationHandlerStop
    parsed = amount_input.parse_amount_text(message.text or "")
    del waiting[key]
    if parsed is None:
        await _ask_amount(context, message, reminder, prompt.index, prompt.user_id,
                          prefix=amount_input.RETRY_TEXT + "\n")
        raise ApplicationHandlerStop
    amount, typed = parsed
    currency = typed.symbol if typed is not None else reminder.payments[prompt.index].preset[
        "currency"]
    await _save(context, message, reminder, prompt.index, amount, currency)
    raise ApplicationHandlerStop


AMOUNT_REPLY = MessageHandler(filters.TEXT & REPLY_TO_PROMPT, on_amount_reply)
