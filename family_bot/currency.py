"""Currency registry: the one place that knows every Currency the bot handles.

Each entry holds the value written to the Data sheet, its ISO 4217 code, the
Russian words a person says (for the voice and receipt prompts) and the word
stems that recognise it in free text and in Regular_expenses column headers.

Buttons (`bot.CURS`), voice/receipt prompts and snapping, preset columns and the
symbol ↔ ISO maps in `sheet_data` are all derived from `CURRENCIES`. Adding a
Currency is one line there.

``format_amount`` is the one way the bot shows an amount: «1 234 дин».
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal


@dataclass(frozen=True)
class Currency:
    symbol: str  # value stored in Data: legacy symbol, or the ISO code for newer ones
    code: str  # ISO 4217
    words: tuple[str, ...]  # spoken forms shown to Claude, e.g. "доллар/долларов→$"
    stems: tuple[str, ...]  # lower-case prefixes that recognise the word in text


# Order is the button order. Long-standing Currencies are stored as symbols,
# any newer one as its ISO code (owner decision Q8 in the revision spec).
CURRENCIES: tuple[Currency, ...] = (
    Currency("₽", "RUB", ("рубль", "рублей"), ("рубл",)),
    Currency("дин", "RSD", ("динар", "динаров"), ("динар",)),
    Currency("€", "EUR", ("евро",), ("евро",)),
    Currency("¥", "JPY", ("иена", "йен"), ("иен", "йен")),
    Currency("£", "GBP", ("фунт", "фунтов", "фунт стерлингов"), ("фунт",)),
    Currency("$", "USD", ("доллар", "долларов"), ("доллар",)),
    Currency("CNY", "CNY", ("юань", "юаней"), ("юан",)),
)


def symbols() -> list[str]:
    """Stored values of all registered Currencies, in button order."""
    return [c.symbol for c in CURRENCIES]


def registered(values: list[str]) -> list[Currency]:
    """Registry entries whose stored symbol is in values, in registry order."""
    return [c for c in CURRENCIES if c.symbol in values]


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


CENT = Decimal("0.01")


def format_amount(amount: float, symbol: str | None = None) -> str:
    """Amount as the bot shows it: «1 234 дин», «12,50 €».

    Thousands are split by a space, the decimal mark is a comma, and the two
    decimals appear only when they are not zero. Rounds half up to cents.
    """
    cents = int(Decimal(str(amount)).quantize(CENT, rounding=ROUND_HALF_UP) * 100)
    whole, frac = divmod(abs(cents), 100)
    text = ("-" if cents < 0 else "") + f"{whole:,}".replace(",", " ")
    if frac:
        text += f",{frac:02d}"
    return f"{text} {symbol}" if symbol else text
