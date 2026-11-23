"""Payment reminder: monthly Presets not yet entered this month (no Telegram).

On the 5th and 20th the bot lists the monthly Presets with no Data row this
month whose comment equals the Preset name (case and outer spaces ignored).
Under each one go the Data rows of the same Category this month, so a person
decides whether the payment is still due; nothing is credited by guess.

A sent reminder lives in ``bot_data`` under a short id, like a Draft card in
``user_data``: a restart wipes it and its buttons answer ``STALE_TEXT``. The
Telegram adapter is ``payment_reminder_bot``.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from typing import Optional

from family_bot.analytics import month_title
from family_bot import look
from family_bot.currency import format_amount
from family_bot.draft_expense import ButtonGrid
from family_bot.expense import ExpenseRecord
from family_bot.sheet_data import Row

# bot_data keys: sent reminders by id; amount prompts by (chat id, message id).
KEY_REMINDERS = "payment_reminders"
KEY_PROMPTS = "payment_reminder_prompts"
CALLBACK_PREFIX = "pr:"
ACTION_PAY = "pay"
ACTION_ALREADY = "already"
PAY_LABEL = "⚡ Внести: {name}"
ALREADY_LABEL = "✅ Уже внесено"
# Reminder id length in random bytes (3 bytes = 6 hex chars).
REMINDER_ID_BYTES = 3

TITLE = "🔔 {month}: ещё не внесены платежи"
ALL_CLOSED_TEXT = "🔔 {month}: все платежи из напоминания разобраны."
HINT = "«⚡ Внести» запишет платёж сегодняшним числом, «✅ Уже внесено» уберёт строку."
STALE_TEXT = "Напоминание устарело после обновления бота"
CLOSED_TEXT = "Этот платёж уже убран из напоминания."
# Text of the amount prompt that a reply must answer; the reply handler looks for it.
PROMPT_MARK = "Ответьте на это сообщение суммой."
PROMPT_TEXT = "💵 Сколько внести за «{name}» ({currency})? " + PROMPT_MARK
NOT_YOURS_TEXT = "Сумму вводит тот, кто нажал «⚡ Внести»."


@dataclass(frozen=True)
class DuePayment:
    """A monthly Preset not entered this month and the month's rows of its Category."""

    preset: dict
    same_category: tuple[Row, ...]


@dataclass
class Reminder:
    """One sent reminder: its payments and which lines are still in the message."""

    id: str
    month: str
    payments: tuple[DuePayment, ...]
    open: set[int] = field(default_factory=set)
    chat_id: Optional[int] = None
    message_id: Optional[int] = None


@dataclass(frozen=True)
class AmountPrompt:
    """«⚡ Внести» on a payment without an amount: who must answer, and for which line."""

    reminder_id: str
    index: int
    user_id: int


def _same_text(a: str, b: str) -> bool:
    return a.strip().casefold() == b.strip().casefold()


def due_payments(presets: list[dict], rows: list[Row], month: str) -> list[DuePayment]:
    """
    Monthly Presets to remind about, in sheet order.

    Parameters
    ----------
    presets : list[dict]
        Presets from ``regular.load_presets`` (``section`` key). A Preset with
        ``remind`` False (the «напоминать» column) is left out.
    rows : list[Row]
        Data rows.
    month : str
        ``YYYY-MM`` of the reminder.

    Returns
    -------
    list[DuePayment]
        Presets with no row this month whose comment is the Preset name.
    """
    this_month = [r for r in rows if r.month == month]
    due = []
    for preset in presets:
        if preset.get("section") != "monthly" or not preset.get("remind", True):
            continue
        if any(_same_text(r.comment, preset["name"]) for r in this_month):
            continue
        same = tuple(r for r in this_month if _same_text(r.category, preset["category"]))
        due.append(DuePayment(preset=preset, same_category=same))
    return due


def new_reminder(payments: list[DuePayment], month: str) -> Reminder:
    """A reminder with every payment line open and a fresh id."""
    return Reminder(id=secrets.token_hex(REMINDER_ID_BYTES), month=month,
                    payments=tuple(payments), open=set(range(len(payments))))


def _payment_lines(payment: DuePayment) -> list[str]:
    preset = payment.preset
    amount = ("сумма не задана" if preset["amount"] is None
              else format_amount(preset["amount"], preset["currency"]))
    lines = [f"• {preset['name']} — {amount} ({preset['category']})"]
    for r in payment.same_category:
        tail = f" — {r.comment}" if r.comment.strip() else ""
        lines.append(f"   уже есть: {r.date_text} — {format_amount(r.amount, r.currency)}{tail}")
    return lines


def message_text(reminder: Reminder) -> str:
    """Reminder text (HTML) with the open lines; a closing line once all are closed."""
    month = month_title(reminder.month)
    if not reminder.open:
        return ALL_CLOSED_TEXT.format(month=month)
    lines = [TITLE.format(month=month), ""]
    for i in sorted(reminder.open):
        lines += _payment_lines(reminder.payments[i])
    return look.escape("\n".join([*lines, "", HINT]))


def buttons(reminder: Reminder) -> ButtonGrid:
    """«⚡ Внести» and «✅ Уже внесено» for each open line."""
    grid: ButtonGrid = []
    for i in sorted(reminder.open):
        name = reminder.payments[i].preset["name"]
        base = f"{CALLBACK_PREFIX}{reminder.id}:{i}:"
        grid.append([(PAY_LABEL.format(name=name), base + ACTION_PAY),
                     (ALREADY_LABEL, base + ACTION_ALREADY)])
    return grid


def parse_callback(data: str) -> Optional[tuple[str, int, str]]:
    """(reminder id, line index, action) from callback data; None if it is not ours."""
    if not data.startswith(CALLBACK_PREFIX):
        return None
    parts = data[len(CALLBACK_PREFIX):].split(":")
    if len(parts) != 3 or not parts[1].isdigit() or parts[2] not in (ACTION_PAY, ACTION_ALREADY):
        return None
    return parts[0], int(parts[1]), parts[2]


def take(reminder: Reminder, index: int) -> Optional[DuePayment]:
    """Close a line; None when it is already closed (another tap was first)."""
    if index not in reminder.open:
        return None
    reminder.open.discard(index)
    return reminder.payments[index]


def put_back(reminder: Reminder, index: int) -> None:
    """Reopen a line after a failed save."""
    reminder.open.add(index)


def record(preset: dict, amount: float, currency: str, today: str) -> ExpenseRecord:
    """The Expense for a payment: sheet values, the Preset name as comment, no Large mark."""
    return ExpenseRecord(date=today, category=preset["category"], amount=amount,
                         currency=currency, who=preset["who"], comment=preset["name"],
                         large=False)


def reminders(bot_data: dict) -> dict[str, Reminder]:
    """Sent reminders with open lines, by id."""
    return bot_data.setdefault(KEY_REMINDERS, {})


def prompts(bot_data: dict) -> dict[tuple[int, int], AmountPrompt]:
    """Amount prompts waiting for a reply, by (chat id, message id)."""
    return bot_data.setdefault(KEY_PROMPTS, {})


def add(bot_data: dict, reminder: Reminder) -> None:
    """Keep a sent reminder so its buttons work."""
    reminders(bot_data)[reminder.id] = reminder


def get(bot_data: dict, reminder_id: str) -> Optional[Reminder]:
    """A kept reminder, or None after a restart."""
    return reminders(bot_data).get(reminder_id)


def forget_if_closed(bot_data: dict, reminder: Reminder) -> None:
    """Drop a reminder with no open lines, and its waiting prompts."""
    if reminder.open:
        return
    reminders(bot_data).pop(reminder.id, None)
    waiting = prompts(bot_data)
    for key in [k for k, p in waiting.items() if p.reminder_id == reminder.id]:
        del waiting[key]
