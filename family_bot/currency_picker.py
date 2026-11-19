"""Currency picker: Recent currencies first, «🌍 Другая» pages, search by text.

Telegram-free and shared by every place the bot asks for a Currency (manual
input, the Draft card; statistics Conversion next). A caller passes ``cb``,
which turns a picker payload into its own callback_data:

- ``pick:<ISO>`` — the User chose this Currency (``picked_code``);
- ``page:<n>`` — show page n of all Currencies (``page_number``).

Recent currencies are per User (Telegram account, Q9): a ``user_data`` cache in
front of the ``Users`` worksheet, so they survive a deploy. A User with no row
gets the three Currencies most frequent in Data, topped up from
``DEFAULT_RECENT`` (Q18). A picked Currency is written to Data as
``currency.stored_value`` — legacy symbol or ISO code (Q8).

Russian names for search come from Babel (CLDR data shipped in the package,
no network): «юань», «лари», «иракский динар». All Currencies are those of the
rate service (``fx.currency_codes``), cached for ``CATALOG_TTL_SEC``.

Sheets and rate-service calls block: callers run them in ``asyncio.to_thread``.
"""
from __future__ import annotations

import time
from collections import Counter
from functools import lru_cache
from typing import Callable, Iterable, MutableMapping, Optional

import requests
from babel.numbers import get_currency_name

from family_bot import currency
from family_bot import errors
from family_bot import fx
from family_bot.sheet_data import parse_rows
from family_bot.sheets import SheetsClient

Grid = list[list[tuple[str, str]]]  # (label, callback_data)
Callback = Callable[[str], str]  # picker payload -> caller's callback_data

# user_data key: this User's Recent currencies (stored values), newest first.
KEY_RECENT = "recent_currencies"
RECENT_COUNT = 3
# Recent currencies of a new User when Data has none (Q18), as ISO codes.
DEFAULT_RECENT = tuple(currency.stored_value(c) for c in ("RSD", "EUR", "RUB"))

OTHER_BUTTON = "🌍 Другая"
PICK = "pick:"
PAGE = "page:"
PAGE_COLUMNS = 4
PAGE_ROWS = 6
PAGE_SIZE = PAGE_COLUMNS * PAGE_ROWS
# Shorter text matches too many Russian names to be useful.
MIN_NAME_QUERY = 3
NAME_LOCALE = "ru"
# Plural counts whose Russian forms are searched: «юань», «юаня», «юаней».
NAME_COUNTS = (None, 1, 2, 5)

CATALOG_TTL_SEC = 3600.0

ASK = "💱 Валюта?"
TYPE_HINT = "Нажмите кнопку или введите код (GEL) или название (юань)."
NOT_FOUND = "❌ Не нашёл такую валюту."
SEVERAL = "💱 Нашлось несколько."
PREV_PAGE, NEXT_PAGE = "◀️", "▶️"


# ---------- search ----------

@lru_cache(maxsize=None)
def _names(code: str) -> tuple[str, ...]:
    """Lower-case Russian names of an ISO code (CLDR via Babel)."""
    return tuple({get_currency_name(code, count=n, locale=NAME_LOCALE).casefold()
                  for n in NAME_COUNTS})


def search(text: str, codes: Iterable[str]) -> list[str]:
    """
    ISO codes matching typed text: stored symbol, ISO code or part of a Russian name.

    Parameters
    ----------
    text : str
        What the User typed: «юань», «GEL», «дин», «иракский динар».
    codes : Iterable[str]
        Currencies that can be chosen (the rate service's list).

    Returns
    -------
    list[str]
        Matching codes in code order; empty when nothing matches.

    Notes
    -----
    A registry word («доллар», «динар», «юань») means the household's own
    Currency (USD, RSD, CNY); «канадский доллар» finds the others.
    """
    available = sorted(set(codes))
    query = text.strip()
    if not query:
        return []
    found = currency.find(query)
    if found is not None and found.code in available:
        return [found.code]
    if query.upper() in available:
        return [query.upper()]
    low = query.casefold()
    if len(low) < MIN_NAME_QUERY:
        return []
    return [c for c in available if any(low in name for name in _names(c))]


# ---------- buttons ----------

def _pick_button(value: str, cb: Callback) -> tuple[str, str]:
    return currency.label(value), cb(PICK + currency.to_iso(value))


def recent_grid(recent: Iterable[str], cb: Callback) -> Grid:
    """Recent currencies in one row, then «🌍 Другая»."""
    return [
        [_pick_button(v, cb) for v in recent],
        [(OTHER_BUTTON, cb(f"{PAGE}0"))],
    ]


def choice_grid(codes: Iterable[str], cb: Callback) -> Grid:
    """Buttons for the given codes, PAGE_COLUMNS per row (search results, one page)."""
    buttons = [_pick_button(currency.stored_value(c), cb) for c in codes]
    return [buttons[i:i + PAGE_COLUMNS] for i in range(0, len(buttons), PAGE_COLUMNS)]


def page_count(codes: Iterable[str]) -> int:
    """Number of «🌍 Другая» pages for these codes (at least 1)."""
    return max(1, -(-len(list(codes)) // PAGE_SIZE))


def page_grid(codes: Iterable[str], page: int, cb: Callback) -> Grid:
    """
    One page of all Currencies in code order, with ◀️ ▶️ that wrap around.

    Parameters
    ----------
    codes : Iterable[str]
        All ISO codes that can be chosen.
    page : int
        Page number from 0; out of range wraps around.
    cb : Callback
        Picker payload → callback_data.

    Returns
    -------
    Grid
        Currency rows, then the arrow row when there is more than one page.
    """
    ordered = sorted(set(codes))
    pages = page_count(ordered)
    page %= pages
    grid = choice_grid(ordered[page * PAGE_SIZE:(page + 1) * PAGE_SIZE], cb)
    if pages > 1:
        grid.append([(PREV_PAGE, cb(f"{PAGE}{(page - 1) % pages}")),
                     (NEXT_PAGE, cb(f"{PAGE}{(page + 1) % pages}"))])
    return grid


def page_text(codes: Iterable[str], page: int) -> str:
    """Message text above a page: its number and the typing hint."""
    pages = page_count(codes)
    return f"🌍 Все валюты, стр. {page % pages + 1} из {pages}. {TYPE_HINT}"


def picked_code(payload: str) -> Optional[str]:
    """ISO code of a ``pick:`` payload, else None."""
    if not payload.startswith(PICK):
        return None
    code = payload[len(PICK):].strip().upper()
    return code or None


def page_number(payload: str) -> Optional[int]:
    """Page of a ``page:`` payload, else None."""
    if not payload.startswith(PAGE):
        return None
    value = payload[len(PAGE):]
    return int(value) if value.isdigit() else None


# ---------- Recent currencies ----------

def push(recent: Iterable[str], value: str) -> list[str]:
    """Recent currencies after choosing value: it goes first, the list keeps three."""
    return [value, *(v for v in recent if v != value)][:RECENT_COUNT]


def _stored(value: str) -> Optional[str]:
    """Data cell → stored value («евро» → «€», «gel» → «GEL»); None if not a Currency."""
    found = currency.find(value)
    if found is not None:
        return found.symbol
    text = value.strip()
    if len(text) == 3 and text.isascii() and text.isalpha():
        return text.upper()
    return None


def _from_data(sheets: SheetsClient) -> list[str]:
    """Three most frequent Currencies in Data, topped up from DEFAULT_RECENT (Q18)."""
    values = sheets.read_data_values()
    rows = parse_rows(values[0], values[1:]) if len(values) > 1 else []
    counts = Counter(s for s in (_stored(r.currency) for r in rows) if s)
    frequent = [v for v, _ in counts.most_common(RECENT_COUNT)]
    return [*frequent, *(v for v in DEFAULT_RECENT if v not in frequent)][:RECENT_COUNT]


def _load(user_data: MutableMapping, sheets: Optional[SheetsClient],
          user_id: int) -> Optional[list[str]]:
    """Recent currencies from cache, Users or Data; None when the read failed."""
    cached = user_data.get(KEY_RECENT)
    if cached:
        return list(cached)
    if sheets is None:
        return list(DEFAULT_RECENT)
    try:
        recent = sheets.read_recent_currencies(user_id) or _from_data(sheets)
    except Exception as e:  # noqa: BLE001 - the picker still works with defaults
        errors.log_exception("Recent currencies read", e)
        return None
    user_data[KEY_RECENT] = recent
    return list(recent)


def load_recent(user_data: MutableMapping, sheets: Optional[SheetsClient],
                user_id: int) -> list[str]:
    """
    This User's Recent currencies: cache, then the Users sheet, then Data.

    Parameters
    ----------
    user_data : MutableMapping
        The User's ``context.user_data`` (holds the cache).
    sheets : Optional[SheetsClient]
        Spreadsheet; None when Google Sheets is not set up.
    user_id : int
        Telegram user id.

    Returns
    -------
    list[str]
        Stored values, newest first. A failed read gives DEFAULT_RECENT and is
        not cached, so the next call tries again.
    """
    recent = _load(user_data, sheets, user_id)
    return recent if recent is not None else list(DEFAULT_RECENT)


def remember(user_data: MutableMapping, sheets: Optional[SheetsClient], user_id: int,
             name: str, value: str) -> None:
    """
    Put a chosen Currency first in Recent currencies and save them to Users.

    Writes only when the list changed. A failed write is logged; the choice
    stays in memory until the next restart. When the saved list could not be
    read, nothing is written, so a Sheets hiccup never overwrites it.

    Parameters
    ----------
    user_data : MutableMapping
        The User's ``context.user_data``.
    sheets : Optional[SheetsClient]
        Spreadsheet; None skips the write.
    user_id : int
        Telegram user id.
    name : str
        Spender name, written next to the id for people reading the sheet.
    value : str
        Stored value of the chosen Currency.
    """
    before = _load(user_data, sheets, user_id)
    if before is None:
        return
    after = push(before, value)
    user_data[KEY_RECENT] = after
    if after == before or sheets is None:
        return
    try:
        sheets.write_recent_currencies(user_id, name, after)
    except Exception as e:  # noqa: BLE001 - an Expense must not fail on this
        errors.log_exception("Recent currencies write", e)


# ---------- all Currencies of the rate service ----------

class Catalog:
    """
    Every ISO code the rate service knows, plus the registry, cached.

    Parameters
    ----------
    fetch : Callable[[], Iterable[str]]
        Reads the codes (network).
    clock : Callable[[], float]
        Monotonic seconds, for the cache age.
    """

    def __init__(self, fetch: Callable[[], Iterable[str]] = fx.currency_codes,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._fetch = fetch
        self._clock = clock
        self._codes: list[str] = []
        self._at = 0.0

    def codes(self) -> list[str]:
        """Sorted codes; when the service fails, the registry codes (not cached)."""
        registry = {c.code for c in currency.CURRENCIES}
        if self._codes and self._clock() - self._at < CATALOG_TTL_SEC:
            return list(self._codes)
        try:
            fetched = {c.upper() for c in self._fetch()}
        except (requests.RequestException, ValueError) as e:
            errors.log_exception("Currency list", e)
            return sorted(registry)
        self._codes = sorted(fetched | registry)
        self._at = self._clock()
        return list(self._codes)


CATALOG = Catalog()
