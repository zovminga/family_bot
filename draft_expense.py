#!/usr/bin/env python3
"""Draft Expense confirmation: card text, button grids, callback transitions.

Voice and receipt photo produce Draft items; this module owns review/edit/save.
Telegram I/O stays in bot adapters — they map button grids to InlineKeyboardMarkup
and call edit_message_*.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from expense import ExpenseRecord, append_expense
from sheet_data import DATE_FMT
from sheets import SheetsClient

# user_data keys (kept as vx_* so in-flight Telegram cards keep working)
KEY_ITEMS = "vx_items"
KEY_TRANSCRIPT = "vx_transcript"
KEY_NOTE = "vx_note"
KEY_HEADER = "vx_header"

CALLBACK_PREFIX = "vx:"

ButtonGrid = list[list[tuple[str, str]]]  # (label, callback_data)


@dataclass
class DraftSession:
    """In-memory Draft the user is reviewing before save."""

    items: list[dict] = field(default_factory=list)
    transcript: str = ""
    note: str = ""
    header: Optional[str] = None

    @classmethod
    def load(cls, user_data: dict) -> "DraftSession":
        return cls(
            items=list(user_data.get(KEY_ITEMS) or []),
            transcript=user_data.get(KEY_TRANSCRIPT) or "",
            note=user_data.get(KEY_NOTE) or "",
            header=user_data.get(KEY_HEADER),
        )

    def store(self, user_data: dict) -> None:
        user_data[KEY_ITEMS] = self.items
        user_data[KEY_TRANSCRIPT] = self.transcript
        user_data[KEY_NOTE] = self.note
        if self.header is None:
            user_data.pop(KEY_HEADER, None)
        else:
            user_data[KEY_HEADER] = self.header

    @staticmethod
    def clear(user_data: dict) -> None:
        for key in (KEY_ITEMS, KEY_TRANSCRIPT, KEY_NOTE, KEY_HEADER):
            user_data.pop(key, None)


@dataclass(frozen=True)
class CallbackOutcome:
    """What the Telegram adapter should do after a draft callback."""

    text: Optional[str] = None
    buttons: Optional[ButtonGrid] = None
    markup_only: bool = False
    clear_session: bool = False


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
        ("✅ Сохранить", f"{CALLBACK_PREFIX}save"),
        ("❌ Отмена", f"{CALLBACK_PREFIX}cancel"),
    ]]
    if len(items) == 1:
        rows.append([
            ("✏️ Категория", f"{CALLBACK_PREFIX}cat:0"),
            ("📅 Дата", f"{CALLBACK_PREFIX}date:0"),
            ("💱 Валюта", f"{CALLBACK_PREFIX}cur:0"),
        ])
    else:
        for i, it in enumerate(items):
            rows.append([(
                f"✏️ #{i + 1} {short(it['comment'], 18)}",
                f"{CALLBACK_PREFIX}ed:{i}",
            )])
    return rows


def item_edit_buttons(i: int) -> ButtonGrid:
    return [
        [
            ("✏️ Категория", f"{CALLBACK_PREFIX}cat:{i}"),
            ("📅 Дата", f"{CALLBACK_PREFIX}date:{i}"),
            ("💱 Валюта", f"{CALLBACK_PREFIX}cur:{i}"),
        ],
        [("⬅️ Назад", f"{CALLBACK_PREFIX}menu")],
    ]


def category_buttons(i: int, categories: list[str]) -> ButtonGrid:
    rows: ButtonGrid = []
    row: list[tuple[str, str]] = []
    for idx, c in enumerate(categories):
        row.append((c, f"{CALLBACK_PREFIX}setcat:{i}:{idx}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([("⬅️ Назад", f"{CALLBACK_PREFIX}menu")])
    return rows


def currency_buttons(i: int, currencies: list[str]) -> ButtonGrid:
    rows: ButtonGrid = [[
        (c, f"{CALLBACK_PREFIX}setcur:{i}:{idx}")
        for idx, c in enumerate(currencies)
    ]]
    rows.append([("⬅️ Назад", f"{CALLBACK_PREFIX}menu")])
    return rows


def date_buttons(i: int) -> ButtonGrid:
    return [
        [
            ("📅 Сегодня", f"{CALLBACK_PREFIX}setdate:{i}:t"),
            ("📆 Вчера", f"{CALLBACK_PREFIX}setdate:{i}:y"),
        ],
        [("⬅️ Назад", f"{CALLBACK_PREFIX}menu")],
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
    for it in items:
        append_expense(client, ExpenseRecord(
            date=it["date"],
            category=it["category"],
            amount=it["amount"],
            currency=it["currency"],
            who=it["who"],
            comment=it["comment"],
        ))


def apply_callback(
    data: str,
    session: DraftSession,
    categories: list[str],
    currencies: list[str],
    client: SheetsClient,
    *,
    now: Optional[datetime] = None,
) -> CallbackOutcome:
    """Apply one draft callback (payload after 'vx:'). Mutates session.items."""
    now = now or datetime.now()
    items = session.items

    if data == "cancel":
        return CallbackOutcome(text="❌ Отменено.", clear_session=True)

    if data == "menu":
        if not items:
            return CallbackOutcome(text="Черновик уже не активен.", clear_session=True)
        return CallbackOutcome(text=card_text(session), buttons=main_buttons(session))

    if data == "save":
        if not items:
            return CallbackOutcome(
                text="Нечего сохранять — черновик пуст.",
                clear_session=True,
            )
        save_drafts(client, items)
        if len(items) == 1:
            it = items[0]
            text = (
                f"✅ Сохранено: {it['comment'] or it['category']} — "
                f"{it['amount']:,.2f} {it['currency']} · {it['date']}"
            )
        else:
            text = f"✅ Сохранено трат: {len(items)}"
        return CallbackOutcome(text=text, clear_session=True)

    parts = data.split(":")
    action = parts[0]
    try:
        i = int(parts[1])
    except (IndexError, ValueError):
        return CallbackOutcome()
    if not (0 <= i < len(items)):
        return CallbackOutcome(text="Черновик изменился, начни заново.", clear_session=True)

    if action == "ed":
        return CallbackOutcome(
            text=card_text(session) + f"\n\n✏️ Правим #{i + 1}",
            buttons=item_edit_buttons(i),
        )
    if action == "cat":
        return CallbackOutcome(buttons=category_buttons(i, categories), markup_only=True)
    if action == "cur":
        return CallbackOutcome(buttons=currency_buttons(i, currencies), markup_only=True)
    if action == "date":
        return CallbackOutcome(buttons=date_buttons(i), markup_only=True)
    if action == "setcat":
        items[i]["category"] = categories[int(parts[2])]
        return CallbackOutcome(text=card_text(session), buttons=main_buttons(session))
    if action == "setcur":
        items[i]["currency"] = currencies[int(parts[2])]
        return CallbackOutcome(text=card_text(session), buttons=main_buttons(session))
    if action == "setdate":
        base = now - timedelta(days=1) if parts[2] == "y" else now
        items[i]["date"] = base.strftime(DATE_FMT)
        return CallbackOutcome(text=card_text(session), buttons=main_buttons(session))

    return CallbackOutcome()
