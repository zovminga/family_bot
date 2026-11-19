#!/usr/bin/env python3
"""Draft Expense confirmation: card text, button grids, callback transitions.

Voice, receipt photo and typed text produce Draft items; this module owns
review/edit/save.
Telegram I/O stays in bot adapters — they map button grids to InlineKeyboardMarkup
and call edit_message_*.
"""
from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from family_bot import clock
from family_bot.expense import ExpenseRecord, append_expenses
from family_bot.sheet_data import DATE_FMT
from family_bot.sheets import SheetsClient

# user_data key: dict draft id -> DraftSession, so several cards live at once.
KEY_DRAFTS = "drafts"

CALLBACK_PREFIX = "vx:"
# Telegram rejects a keyboard whose callback_data exceeds 64 bytes.
CALLBACK_DATA_MAX_BYTES = 64
# Draft id length in random bytes (2 bytes = 4 hex chars).
DRAFT_ID_BYTES = 2
# Hex chars of the category-name hash used when the name itself is too long.
CATEGORY_HASH_CHARS = 8
# Saved / cancelled drafts kept so a repeat tap on their card is a no-op.
MAX_CLOSED_DRAFTS = 20

ButtonGrid = list[list[tuple[str, str]]]  # (label, callback_data)


@dataclass
class DraftSession:
    """In-memory Draft the user is reviewing before save."""

    items: list[dict] = field(default_factory=list)
    transcript: str = ""
    note: str = ""
    header: Optional[str] = None
    id: str = ""
    closed: bool = False  # saved or cancelled: its card buttons do nothing


def add_draft(user_data: dict, session: DraftSession) -> DraftSession:
    """Give the Draft a fresh short id and keep it next to the other open Drafts."""
    drafts: dict[str, DraftSession] = user_data.setdefault(KEY_DRAFTS, {})
    draft_id = secrets.token_hex(DRAFT_ID_BYTES)
    while draft_id in drafts:
        draft_id = secrets.token_hex(DRAFT_ID_BYTES)
    session.id = draft_id
    drafts[draft_id] = session
    closed = [k for k, d in drafts.items() if d.closed]
    for k in closed[:-MAX_CLOSED_DRAFTS]:
        del drafts[k]
    return session


def get_draft(user_data: dict, draft_id: str) -> Optional[DraftSession]:
    return (user_data.get(KEY_DRAFTS) or {}).get(draft_id)


def parse_callback(callback_data: str) -> tuple[str, str]:
    """Split 'vx:<id>:<payload>' into (id, payload); ('', '') if malformed."""
    if not callback_data.startswith(CALLBACK_PREFIX):
        return "", ""
    draft_id, _, payload = callback_data[len(CALLBACK_PREFIX):].partition(":")
    if not draft_id or not payload:
        return "", ""
    return draft_id, payload


def _cb(session: DraftSession, payload: str) -> str:
    return f"{CALLBACK_PREFIX}{session.id}:{payload}"


@dataclass(frozen=True)
class CallbackOutcome:
    """What the Telegram adapter should do after a draft callback."""

    text: Optional[str] = None
    buttons: Optional[ButtonGrid] = None
    markup_only: bool = False


def short(text: str, n: int = 20) -> str:
    text = (text or "—").strip() or "—"
    return text if len(text) <= n else text[: n - 1] + "…"


def card_text(session: DraftSession) -> str:
    items = session.items
    lines = (
        [session.header, ""]
        if session.header
        else [f"🎧 Расшифровка: «{session.transcript}»", ""]
    )
    if len(items) == 1:
        it = items[0]
        lines += [
            "📝 Черновик траты",
            f"• Товар/коммент: {it['comment'] or '—'}",
            f"• Сумма: {it['amount']:,.2f} {it['currency']}",
            f"• Категория: {it['category']}",
            f"• Дата: {it['date']}",
            f"• Кто: {it['who']}",
        ]
    else:
        lines.append(f"📝 Черновик: {len(items)} трат(ы)")
        for i, it in enumerate(items, 1):
            lines.append(
                f"{i}. {it['comment'] or '—'} — {it['amount']:,.2f} {it['currency']}"
                f" · {it['category']} · {it['date']}"
            )
    if session.note:
        lines += ["", f"ℹ️ {session.note}"]
    lines += ["", "Проверь и сохрани 👇"]
    return "\n".join(lines)


def main_buttons(session: DraftSession) -> ButtonGrid:
    items = session.items
    rows: ButtonGrid = [[
        ("✅ Сохранить", _cb(session, "save")),
        ("❌ Отмена", _cb(session, "cancel")),
    ]]
    if len(items) == 1:
        rows.append([
            ("✏️ Категория", _cb(session, "cat:0")),
            ("📅 Дата", _cb(session, "date:0")),
            ("💱 Валюта", _cb(session, "cur:0")),
        ])
    else:
        for i, it in enumerate(items):
            rows.append([(
                f"✏️ #{i + 1} {short(it['comment'], 18)}",
                _cb(session, f"ed:{i}"),
            )])
    return rows


def item_edit_buttons(session: DraftSession, i: int) -> ButtonGrid:
    return [
        [
            ("✏️ Категория", _cb(session, f"cat:{i}")),
            ("📅 Дата", _cb(session, f"date:{i}")),
            ("💱 Валюта", _cb(session, f"cur:{i}")),
        ],
        [("⬅️ Назад", _cb(session, "menu"))],
    ]


def _category_hash(name: str) -> str:
    return hashlib.sha1(name.encode()).hexdigest()[:CATEGORY_HASH_CHARS]


def _category_cb(session: DraftSession, i: int, name: str) -> str:
    """Carry the category name, so a reordered Config cannot change the pick.

    A name too long for Telegram's callback_data limit is carried as a short
    hash of the name ('setcathash'), matched against the current categories.
    """
    by_name = _cb(session, f"setcat:{i}:{name}")
    if len(by_name.encode()) <= CALLBACK_DATA_MAX_BYTES:
        return by_name
    return _cb(session, f"setcathash:{i}:{_category_hash(name)}")


def category_buttons(session: DraftSession, i: int, categories: list[str]) -> ButtonGrid:
    rows: ButtonGrid = []
    row: list[tuple[str, str]] = []
    for c in categories:
        row.append((c, _category_cb(session, i, c)))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([("⬅️ Назад", _cb(session, "menu"))])
    return rows


def currency_buttons(session: DraftSession, i: int, currencies: list[str]) -> ButtonGrid:
    rows: ButtonGrid = [[
        (c, _cb(session, f"setcur:{i}:{idx}"))
        for idx, c in enumerate(currencies)
    ]]
    rows.append([("⬅️ Назад", _cb(session, "menu"))])
    return rows


def date_buttons(session: DraftSession, i: int) -> ButtonGrid:
    return [
        [
            ("📅 Сегодня", _cb(session, f"setdate:{i}:t")),
            ("📆 Вчера", _cb(session, f"setdate:{i}:y")),
        ],
        [("⬅️ Назад", _cb(session, "menu"))],
    ]


def session_from_voice(
    items: list[dict],
    *,
    who: str,
    transcript: str,
    note: str = "",
) -> DraftSession:
    for it in items:
        it["who"] = who
    return DraftSession(items=items, transcript=transcript, note=note, header=None)


def session_from_text(
    items: list[dict],
    *,
    who: str,
    text: str,
    note: str = "",
) -> DraftSession:
    for it in items:
        it["who"] = who
    return DraftSession(items=items, transcript=text, note=note, header=f"✍️ «{text}»")


def session_from_receipt(
    items: list[dict],
    *,
    who: str,
    merchant: str = "",
    note: str = "",
) -> DraftSession:
    for it in items:
        it["who"] = who
    header = "🧾 Чек" + (f" · {merchant}" if merchant else "") + " — черновик"
    return DraftSession(
        items=items,
        transcript=merchant,
        note=note,
        header=header,
    )


def save_drafts(client: SheetsClient, items: list[dict]) -> None:
    """Write all items in one append call: it either lands whole or not at all."""
    append_expenses(client, [
        ExpenseRecord(
            date=it["date"],
            category=it["category"],
            amount=it["amount"],
            currency=it["currency"],
            who=it["who"],
            comment=it["comment"],
        )
        for it in items
    ])


def apply_callback(
    data: str,
    session: DraftSession,
    categories: list[str],
    currencies: list[str],
    client: SheetsClient,
    *,
    now: Optional[datetime] = None,
) -> CallbackOutcome:
    """Apply one draft callback (payload after 'vx:<id>:'). Mutates session.

    A closed (saved or cancelled) Draft ignores every tap, so a double tap
    keeps «✅ Сохранено» and writes nothing twice. A failed save leaves the
    Draft open for a retry.
    """
    now = now or clock.now()
    items = session.items

    if session.closed:
        return CallbackOutcome()

    if data == "cancel":
        session.closed = True
        return CallbackOutcome(text="❌ Отменено.")

    if data == "menu":
        return CallbackOutcome(text=card_text(session), buttons=main_buttons(session))

    if data == "save":
        if not items:
            session.closed = True
            return CallbackOutcome(text="Нечего сохранять — черновик пуст.")
        save_drafts(client, items)
        session.closed = True
        if len(items) == 1:
            it = items[0]
            text = (
                f"✅ Сохранено: {it['comment'] or it['category']} — "
                f"{it['amount']:,.2f} {it['currency']} · {it['date']}"
            )
        else:
            text = f"✅ Сохранено трат: {len(items)}"
        return CallbackOutcome(text=text)

    parts = data.split(":", 2)
    action = parts[0]
    try:
        i = int(parts[1])
    except (IndexError, ValueError):
        return CallbackOutcome()
    if not (0 <= i < len(items)):
        return CallbackOutcome(text="Черновик изменился, начни заново.")
    arg = parts[2] if len(parts) > 2 else ""

    if action == "ed":
        return CallbackOutcome(
            text=card_text(session) + f"\n\n✏️ Правим #{i + 1}",
            buttons=item_edit_buttons(session, i),
        )
    if action == "cat":
        return CallbackOutcome(
            buttons=category_buttons(session, i, categories), markup_only=True)
    if action == "cur":
        return CallbackOutcome(
            buttons=currency_buttons(session, i, currencies), markup_only=True)
    if action == "date":
        return CallbackOutcome(buttons=date_buttons(session, i), markup_only=True)
    if action == "setcat" and arg:
        items[i]["category"] = arg
        return CallbackOutcome(text=card_text(session), buttons=main_buttons(session))
    if action == "setcathash":
        match = [c for c in categories if _category_hash(c) == arg]
        if not match:  # category removed from Config since the card was shown
            return CallbackOutcome()
        items[i]["category"] = match[0]
        return CallbackOutcome(text=card_text(session), buttons=main_buttons(session))
    if action == "setcur":
        items[i]["currency"] = currencies[int(arg)]
        return CallbackOutcome(text=card_text(session), buttons=main_buttons(session))
    if action == "setdate":
        base = now - timedelta(days=1) if arg == "y" else now
        items[i]["date"] = base.strftime(DATE_FMT)
        return CallbackOutcome(text=card_text(session), buttons=main_buttons(session))

    return CallbackOutcome()
