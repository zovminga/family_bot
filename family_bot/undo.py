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
from typing import Callable, Sequence

from family_bot import look
from family_bot.currency import format_amount
from family_bot.expense import ExpenseRecord, delete_expenses
from family_bot.sheet_data import DATE_FMT, parse_amount
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
SAVED_TITLE = "✅ Сохранено"
UNDONE_TITLE = "↩️ Отменено"
# Day and month of a record in the saved / undone lines («23.11»).
SHORT_DATE_FMT = "%d.%m"

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
    ``drop_button``: the button is dead for everyone (the undo window is over),
    so the adapter removes it and keeps the message text.
    """

    text: str
    done: bool
    drop_button: bool = False


def remember(user_data: dict, records: list[ExpenseRecord], now: datetime) -> look.ButtonGrid:
    """Keep this save as the User's last one; return its undo button (red)."""
    save = LastSave(id=secrets.token_hex(SAVE_ID_BYTES), records=tuple(records), saved_at=now)
    user_data[KEY_LAST_SAVE] = save
    return [[(UNDO_LABEL, CALLBACK_PREFIX + save.id, look.DANGER)]]


def _short_date(text: str) -> str:
    try:
        return datetime.strptime(text, DATE_FMT).strftime(SHORT_DATE_FMT)
    except ValueError:
        return text


def _record_lines(records: Sequence[ExpenseRecord], mark: Callable[[str], str]) -> str:
    """
    One line per record: «Кофе — 350 дин» marked, then Category · date · Spender.

    Parameters
    ----------
    records : Sequence[ExpenseRecord]
        Saved records.
    mark : Callable[[str], str]
        ``look.bold`` or ``look.strike``: escapes and marks «what — amount».

    Returns
    -------
    str
        HTML. A single record takes two lines (as on the Draft card), several
        records one line each.
    """
    lines = []
    for r in records:
        amount = parse_amount(r.amount)
        shown = format_amount(amount, r.currency) if amount is not None else str(r.amount)
        what = r.comment or r.category
        details = [r.category] if r.comment else []
        details += [_short_date(r.date), r.who]
        tail = look.escape(" · ".join(d for d in details if d))
        head = mark(f"{what} — {shown}")
        lines.append(f"{head}\n{tail}" if len(records) == 1 else f"{head} · {tail}")
    return "\n".join(lines)


def saved_text(records: Sequence[ExpenseRecord]) -> str:
    """«✅ Сохранено» with the saved records in a quote (HTML)."""
    return f"{SAVED_TITLE}\n{look.quote(_record_lines(records, look.bold))}"


def undone_text(records: Sequence[ExpenseRecord]) -> str:
    """«↩️ Отменено» with the deleted records struck through (HTML)."""
    return f"{UNDONE_TITLE}\n{look.quote(_record_lines(records, look.strike))}"


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
            "Удалите строку в таблице.", done=False, drop_button=True)
    del user_data[KEY_LAST_SAVE]
    try:
        deleted = delete_expenses(client, list(save.records))
    except Exception:
        user_data[KEY_LAST_SAVE] = save
        raise
    if not deleted:
        return UndoResult(
            "Запись уже изменена или удалена в таблице, ничего не удалил.", done=True)
    return UndoResult(undone_text(save.records), done=True)
