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
from typing import Callable, Optional, Sequence

from family_bot import amount_input
from family_bot import clock
from family_bot import currency
from family_bot import currency_picker as picker
from family_bot import look
from family_bot import undo
from family_bot.currency import format_amount
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

ButtonGrid = look.ButtonGrid

# Reply to a card whose Draft is not in memory. Drafts live only in memory, so
# that is a card from before a restart (or a closed Draft past MAX_CLOSED_DRAFTS).
STALE_TEXT = "Черновик устарел после обновления бота. Отправь трату ещё раз"

# Item key of the Large expense flag; an item without it is not large.
LARGE_KEY = "large"
LARGE_MARK = "🔶"
LARGE_BUTTON = f"{LARGE_MARK} Крупная"
LARGE_LINE = f"{LARGE_MARK} Крупная трата"
DRAFT_TITLE = "Черновик траты"
CANCELLED_TEXT = "Черновик отменён"
# Shown on the card under the fields when a typed amount fails the checks.
AMOUNT_ERROR = "❌ Нужна сумма больше нуля, не больше двух знаков после запятой."

# (amount, Currency) -> whether to suggest the Large expense mark.
IsLarge = Callable[[float, str], bool]


@dataclass
class DraftSession:
    """In-memory Draft the user is reviewing before save."""

    items: list[dict] = field(default_factory=list)
    transcript: str = ""  # voice, typed text or «Чек · shop»: shown in a quote
    note: str = ""
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
    # Records just written by «✅ Сохранить» (the adapter offers «↩️ Отменить»).
    saved: tuple[ExpenseRecord, ...] = ()
    # Item index whose new amount the User types next («✏️ Сумма»).
    await_amount: Optional[int] = None
    # Item index whose Currency the User may type next («🌍 Другая», search).
    await_currency: Optional[int] = None
    # Stored value of a Currency the User just chose (goes to Recent currencies).
    picked: Optional[str] = None


def mark_large(session: DraftSession, is_large: IsLarge) -> None:
    """
    Set the Large expense flag of every item by the threshold rule.

    Parameters
    ----------
    session : DraftSession
        New Draft; its items are changed in place.
    is_large : IsLarge
        ``large_expense.suggest`` with the threshold, base Currency and rate
        already bound (blocking: may read rates).
    """
    for it in session.items:
        it[LARGE_KEY] = is_large(it["amount"], it["currency"])


def _is_large(item: dict) -> bool:
    return bool(item.get(LARGE_KEY))


def short(text: str, n: int = 20) -> str:
    text = (text or "—").strip() or "—"
    return text if len(text) <= n else text[: n - 1] + "…"


def _h(text: str) -> str:
    return look.escape(str(text))


def _title(session: DraftSession) -> str:
    n = len(session.items)
    name = DRAFT_TITLE if n == 1 else f"Черновик: {look.expenses(n)}"
    return f"📝 {look.bold(name)}"


def _totals(items: list[dict]) -> str:
    """Sum per Currency in first-seen order: «1 200 дин + 20 €» (Currencies do not add up)."""
    sums: dict[str, float] = {}
    for it in items:
        sums[it["currency"]] = sums.get(it["currency"], 0.0) + it["amount"]
    return " + ".join(format_amount(amount, cur) for cur, amount in sums.items())


def card_text(session: DraftSession) -> str:
    """
    Draft card as Telegram HTML: title, source in a quote, fields; human text escaped.

    Parameters
    ----------
    session : DraftSession
        Draft to show; ``transcript`` (voice, typed text or receipt) goes in the quote.

    Returns
    -------
    str
        One item: «Сумма: <b>350 дин</b>» lines. Several: «1. Хлеб — <b>120 дин</b>»
        lines and the total under them.
    """
    items = session.items
    lines = [_title(session)]
    if session.transcript:
        lines.append(look.quote(_h(session.transcript)))
    if len(items) == 1:
        it = items[0]
        lines += [
            look.field("Сумма", format_amount(it["amount"], it["currency"])),
            look.field("Что", it["comment"] or "—"),
            look.field("Категория", it["category"]),
            look.field("Дата", it["date"]),
            look.field("Кто", it["who"]),
        ]
        if _is_large(it):
            lines.append(LARGE_LINE)
    else:
        for i, it in enumerate(items, 1):
            mark = f"{LARGE_MARK} " if _is_large(it) else ""
            amount = look.bold(format_amount(it["amount"], it["currency"]))
            lines.append(f"{i}. {mark}{_h(it['comment'] or '—')} — {amount}"
                         f" · {_h(it['category'])} · {_h(it['date'])}")
        lines += ["", look.field("Итого", _totals(items))]
    if session.note:
        lines += ["", _h(session.note)]
    return "\n".join(lines)


def main_buttons(session: DraftSession) -> ButtonGrid:
    items = session.items
    rows: ButtonGrid = [[
        ("✅ Сохранить", _cb(session, "save"), look.SUCCESS),
        ("❌ Отмена", _cb(session, "cancel"), look.DANGER),
    ]]
    if len(items) == 1:
        rows.append([
            ("🏷 Категория", _cb(session, "cat:0")),
            ("📅 Дата", _cb(session, "date:0")),
            ("💱 Валюта", _cb(session, "cur:0")),
        ])
        rows.append(_amount_who_row(session, 0))
    else:
        for i, it in enumerate(items):
            rows.append([(
                f"✏️ #{i + 1} {short(it['comment'], 18)}",
                _cb(session, f"ed:{i}"),
            )])
    return rows


def _amount_who_row(session: DraftSession, i: int) -> list[look.Button]:
    return [
        ("✏️ Сумма", _cb(session, f"amt:{i}")),
        ("👤 Кто", _cb(session, f"who:{i}")),
        (LARGE_BUTTON, _cb(session, f"large:{i}")),
    ]


def item_edit_buttons(session: DraftSession, i: int) -> ButtonGrid:
    return [
        [
            ("🏷 Категория", _cb(session, f"cat:{i}")),
            ("📅 Дата", _cb(session, f"date:{i}")),
            ("💱 Валюта", _cb(session, f"cur:{i}")),
        ],
        _amount_who_row(session, i),
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
    row: list[look.Button] = []
    for c in categories:
        row.append((c, _category_cb(session, i, c)))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([("⬅️ Назад", _cb(session, "menu"))])
    return rows


def _currency_cb(session: DraftSession, i: int) -> picker.Callback:
    return lambda payload: _cb(session, f"cur:{i}:{payload}")


def _back_row(session: DraftSession) -> list[look.Button]:
    return [("⬅️ Назад", _cb(session, "menu"))]


def currency_buttons(session: DraftSession, i: int, recent: Sequence[str]) -> ButtonGrid:
    """Recent currencies of the User and «🌍 Другая» (``currency_picker``)."""
    return [*picker.recent_grid(recent, _currency_cb(session, i)), _back_row(session)]


def _currency_prompt(session: DraftSession, i: int, line: str) -> str:
    target = f" для #{i + 1}" if len(session.items) > 1 else ""
    return card_text(session) + f"\n\n💱 Валюта{target}. {line}"


def _currency_page(session: DraftSession, i: int, codes: Sequence[str],
                   page: int, line: Optional[str] = None) -> CallbackOutcome:
    grid = picker.page_grid(codes, page, _currency_cb(session, i))
    return CallbackOutcome(
        text=_currency_prompt(session, i, line or picker.page_text(codes, page)),
        buttons=[*grid, _back_row(session)],
        await_currency=i,
    )


def _set_currency(session: DraftSession, i: int, code: str) -> CallbackOutcome:
    value = currency.stored_value(code)
    session.items[i]["currency"] = value
    return CallbackOutcome(text=card_text(session), buttons=main_buttons(session),
                           picked=value)


def search_currency(session: DraftSession, i: int, text: str,
                    codes: Sequence[str]) -> CallbackOutcome:
    """
    Typed Currency for item i after «🌍 Другая»: one match sets it, several are buttons.

    Parameters
    ----------
    session : DraftSession
        Open Draft.
    i : int
        Item index.
    text : str
        Typed code or Russian name («GEL», «юань»).
    codes : Sequence[str]
        Every ISO code that can be chosen.

    Returns
    -------
    CallbackOutcome
        New card text and buttons; ``picked`` set when the Currency changed.
    """
    if not (0 <= i < len(session.items)):
        return CallbackOutcome()
    found = picker.search(text, codes)
    if len(found) == 1:
        return _set_currency(session, i, found[0])
    if not found:
        return _currency_page(session, i, codes, 0, f"{picker.NOT_FOUND} {picker.TYPE_HINT}")
    grid = picker.choice_grid(found[:picker.PAGE_SIZE], _currency_cb(session, i))
    return CallbackOutcome(
        text=_currency_prompt(session, i, f"{picker.SEVERAL} {picker.TYPE_HINT}"),
        buttons=[*grid, _back_row(session)],
        await_currency=i,
    )


def spender_buttons(session: DraftSession, i: int, spenders: Sequence[str]) -> ButtonGrid:
    """One button per Allowlist Spender name.

    The callback carries the index, not the name: a long Cyrillic name would
    break the 64-byte limit. Drafts and the Allowlist both live only until a
    restart, so the index points at the same name when tapped.
    """
    rows: ButtonGrid = [[
        (name, _cb(session, f"setwho:{i}:{idx}")) for idx, name in enumerate(spenders)
    ]]
    rows.append([("⬅️ Назад", _cb(session, "menu"))])
    return rows


def amount_prompt(session: DraftSession, i: int, error: str = "") -> str:
    target = f" для #{i + 1}" if len(session.items) > 1 else ""
    reason = f"\n{error}" if error else ""
    return (card_text(session) + reason
            + f"\n\n✏️ Введи новую сумму{target}. Например: 1 200 или 350 дин")


def amount_step(session: DraftSession, i: int, error: str = "") -> CallbackOutcome:
    """
    The «✏️ Сумма» step of item i: card, prompt and «⬅️ Назад».

    Parameters
    ----------
    session : DraftSession
        Open Draft.
    i : int
        Item index whose amount the User types next.
    error : str
        Why the last typed amount was refused (``AMOUNT_ERROR``); empty on the first ask.

    Returns
    -------
    CallbackOutcome
        Card text with the prompt, the back button, ``await_amount`` set.
    """
    return CallbackOutcome(
        text=amount_prompt(session, i, error),
        buttons=[_back_row(session)],
        await_amount=i,
    )


def set_amount(session: DraftSession, i: int, text: str, currencies: list[str]) -> bool:
    """Apply a typed amount to item i; False (nothing changed) if it fails the checks.

    Same rules as the manual flow (`amount_input.parse_amount_text`). A Currency
    typed with the amount replaces the item's Currency if this install offers it.
    """
    parsed = amount_input.parse_amount_text(text)
    if parsed is None or not (0 <= i < len(session.items)):
        return False
    amount, typed = parsed
    session.items[i]["amount"] = amount
    if typed is not None and typed.symbol in currencies:
        session.items[i]["currency"] = typed.symbol
    return True


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
    return DraftSession(items=items, transcript=transcript, note=note)


def session_from_text(
    items: list[dict],
    *,
    who: str,
    text: str,
    note: str = "",
) -> DraftSession:
    for it in items:
        it["who"] = who
    return DraftSession(items=items, transcript=text, note=note)


def session_from_receipt(
    items: list[dict],
    *,
    who: str,
    merchant: str = "",
    note: str = "",
) -> DraftSession:
    for it in items:
        it["who"] = who
    source = "Чек" + (f" · {merchant}" if merchant else "")
    return DraftSession(items=items, transcript=source, note=note)


def save_drafts(client: SheetsClient, items: list[dict]) -> list[ExpenseRecord]:
    """Write all items in one append call: it either lands whole or not at all."""
    records = [
        ExpenseRecord(
            date=it["date"],
            category=it["category"],
            amount=it["amount"],
            currency=it["currency"],
            who=it["who"],
            comment=it["comment"],
            large=_is_large(it),
        )
        for it in items
    ]
    append_expenses(client, records)
    return records


def _registry_codes() -> list[str]:
    return [c.code for c in currency.CURRENCIES]


def apply_callback(
    data: str,
    session: DraftSession,
    categories: list[str],
    recent: Sequence[str],
    client: SheetsClient,
    *,
    now: Optional[datetime] = None,
    spenders: Sequence[str] = (),
    all_codes: Callable[[], Sequence[str]] = _registry_codes,
) -> CallbackOutcome:
    """Apply one draft callback (payload after 'vx:<id>:'). Mutates session.

    A closed (saved or cancelled) Draft ignores every tap, so a double tap
    keeps «✅ Сохранено» and writes nothing twice. A failed save leaves the
    Draft open for a retry.

    ``recent`` are the User's Recent currencies shown by «💱 Валюта»;
    ``all_codes`` lists every Currency for «🌍 Другая» (blocking: rate service).
    A chosen Currency comes back as ``CallbackOutcome.picked``.
    """
    now = now or clock.now()
    items = session.items

    if session.closed:
        return CallbackOutcome()

    if data == "cancel":
        session.closed = True
        return CallbackOutcome(text=CANCELLED_TEXT)

    if data == "menu":
        return CallbackOutcome(text=card_text(session), buttons=main_buttons(session))

    if data == "save":
        if not items:
            session.closed = True
            return CallbackOutcome(text="Нечего сохранять — черновик пуст.")
        # Close before the write: the save runs in a worker thread, so a tap that
        # arrives meanwhile must already see a closed Draft. Reopen on failure.
        session.closed = True
        try:
            records = save_drafts(client, items)
        except Exception:
            session.closed = False
            raise
        return CallbackOutcome(text=undo.saved_text(records), saved=tuple(records))

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
        code = picker.picked_code(arg)
        if code is not None:
            return _set_currency(session, i, code)
        page = picker.page_number(arg)
        if page is not None:
            return _currency_page(session, i, all_codes(), page)
        return CallbackOutcome(
            text=_currency_prompt(session, i, picker.TYPE_HINT),
            buttons=currency_buttons(session, i, recent),
            await_currency=i,
        )
    if action == "date":
        return CallbackOutcome(buttons=date_buttons(session, i), markup_only=True)
    if action == "amt":
        return amount_step(session, i)
    if action == "who":
        return CallbackOutcome(
            buttons=spender_buttons(session, i, spenders), markup_only=True)
    if action == "large":
        items[i][LARGE_KEY] = not _is_large(items[i])
        return CallbackOutcome(text=card_text(session), buttons=main_buttons(session))
    if action == "setwho":
        if not arg.isdigit() or int(arg) >= len(spenders):
            return CallbackOutcome()
        items[i]["who"] = spenders[int(arg)]
        return CallbackOutcome(text=card_text(session), buttons=main_buttons(session))
    if action == "setcat" and arg:
        items[i]["category"] = arg
        return CallbackOutcome(text=card_text(session), buttons=main_buttons(session))
    if action == "setcathash":
        match = [c for c in categories if _category_hash(c) == arg]
        if not match:  # category removed from Config since the card was shown
            return CallbackOutcome()
        items[i]["category"] = match[0]
        return CallbackOutcome(text=card_text(session), buttons=main_buttons(session))
    if action == "setdate":
        base = now - timedelta(days=1) if arg == "y" else now
        items[i]["date"] = base.strftime(DATE_FMT)
        return CallbackOutcome(text=card_text(session), buttons=main_buttons(session))

    return CallbackOutcome()
