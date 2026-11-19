"""Currency registry: the one place that knows every Currency the bot handles.

Each entry holds the value written to the Data sheet, its ISO 4217 code, the
Russian words a person says (for the voice and receipt prompts) and the word
stems that recognise it in free text and in Regular_expenses column headers.

Buttons (`bot.CURS`), voice/receipt prompts and snapping, preset columns and the
symbol ↔ ISO maps in `sheet_data` are all derived from `CURRENCIES`. Adding a
Currency is one line there.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Currency:
    symbol: str  # value stored in Data: legacy symbol, or the ISO code for newer ones
    code: str  # ISO 4217
    words: tuple[str, ...]  # spoken forms shown to Claude, e.g. "доллар/долларов→$"
    stems: tuple[str, ...]  # lower-case prefixes that recognise the word in text


# Order is the button order. The first six are stored as symbols, newer ones as ISO codes.
CURRENCIES: tuple[Currency, ...] = (
    Currency("дин", "RSD", ("динар", "динаров"), ("динар",)),
    Currency("€", "EUR", ("евро",), ("евро",)),
    Currency("₽", "RUB", ("рубль", "рублей"), ("рубл",)),
    Currency("¥", "JPY", ("иена", "йен"), ("иен", "йен")),
    Currency("£", "GBP", ("фунт", "фунтов", "фунт стерлингов"), ("фунт",)),
    Currency("$", "USD", ("доллар", "долларов"), ("доллар",)),
    Currency("CNY", "CNY", ("юань", "юаней"), ("юан",)),
)


def symbols() -> list[str]:
    """Stored values of all registered Currencies, in button order."""
    return [c.symbol for c in CURRENCIES]


def find(value: str | None) -> Currency | None:
    """Look a Currency up by stored symbol, ISO code (any case) or Russian word."""
    text = (value or "").strip()
    if not text:
        return None
    low = text.lower()
    for c in CURRENCIES:
        if low in (c.symbol.lower(), c.code.lower()):
            return c
    for c in CURRENCIES:
        if any(low.startswith(stem) for stem in c.stems):
            return c
    return None


def to_iso(value: str) -> str:
    """ISO code for a stored symbol or code; an unknown 3-letter value is upper-cased."""
    found = find(value)
    if found is not None:
        return found.code
    return value.upper() if len(value) == 3 else value
