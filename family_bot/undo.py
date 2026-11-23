#!/usr/bin/env python3
"""Undo of a User's last save: the «↩️ Отменить» button under «✅ Сохранено».

The save (manual flow or Draft card) calls ``remember``; the button callback
calls ``undo``. Only the User's own latest save can be undone, and only within
UNDO_WINDOW: the record lives in that User's ``user_data`` (Telegram keeps one
per account, so another User's tap finds nothing), and each new save replaces
it. The row is found again by its values (``expense.delete_expenses``).
Telegram I/O stays in the bot adapter.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from family_bot import look
from family_bot.currency import format_amount
from family_bot.draft_expense import ButtonGrid
from family_bot.expense import ExpenseRecord, delete_expenses
from family_bot.sheet_data import parse_amount
from family_bot.sheets import SheetsClient

# user_data key: the User's last save (LastSave).
KEY_LAST_SAVE = "last_save"
CALLBACK_PREFIX = "undo:"
UNDO_LABEL = "↩️ Отменить"
# A save older than this is "old": undoing it needs the sheet.
UNDO_WINDOW_MINUTES = 30
UNDO_WINDOW = timedelta(minutes=UNDO_WINDOW_MINUTES)
# Save id length in random bytes (4 bytes = 8 hex chars).
SAVE_ID_BYTES = 4

@dataclass(frozen=True)
class LastSave:
    """What one save wrote, so it can be found and deleted again."""

    id: str
    records: tuple[ExpenseRecord, ...]
    saved_at: datetime


@dataclass(frozen=True)
class UndoResult:
    """Text for the User; done means the button has no further use.

    A done text replaces the message (HTML); the others go to a plain alert.
    """

    text: str
    done: bool


def remember(user_data: dict, records: list[ExpenseRecord], now: datetime) -> ButtonGrid:
    """Keep this save as the User's last one; return its undo button."""
    save = LastSave(id=secrets.token_hex(SAVE_ID_BYTES), records=tuple(records), saved_at=now)
    user_data[KEY_LAST_SAVE] = save
    return [[(UNDO_LABEL, CALLBACK_PREFIX + save.id)]]


def _done_text(records: tuple[ExpenseRecord, ...]) -> str:
    if len(records) == 1:
        r = records[0]
        amount = parse_amount(r.amount)
        shown = format_amount(amount, r.currency) if amount is not None else str(r.amount)
        return look.escape(f"↩️ Отменено, трата удалена: {r.category} — {shown}, {r.date}")
    return f"↩️ Отменено, удалено трат: {len(records)}"


def undo(user_data: dict, callback_data: str, client: SheetsClient,
         now: datetime) -> UndoResult:
    """Delete the rows of the User's last save if the button belongs to it.

    The save is taken out of user_data before the delete, so a second tap
    does nothing; a failed delete puts it back for a retry.
    """
    save = user_data.get(KEY_LAST_SAVE)
    if not isinstance(save, LastSave) or callback_data != CALLBACK_PREFIX + save.id:
        return UndoResult(
            "Отменить можно только свою последнюю трату, пока бот не перезапускался. "
            "Удалите строку в таблице.", done=False)
    if now - save.saved_at > UNDO_WINDOW:
        return UndoResult(
            f"Отменить можно только в первые {UNDO_WINDOW_MINUTES} минут после сохранения. "
            "Удалите строку в таблице.", done=False)
    del user_data[KEY_LAST_SAVE]
    try:
        deleted = delete_expenses(client, list(save.records))
    except Exception:
        user_data[KEY_LAST_SAVE] = save
        raise
    if not deleted:
        return UndoResult(
            "Запись уже изменена или удалена в таблице, ничего не удалил.", done=True)
    return UndoResult(_done_text(save.records), done=True)
