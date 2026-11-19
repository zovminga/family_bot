"""Currency registry: the one place that knows every Currency the bot handles.

Each entry holds the value written to the Data sheet, its ISO 4217 code, the
Russian words a person says (for the voice and receipt prompts) and the word
stems that recognise it in free text and in Regular_expenses column headers.

Buttons (`bot.CURS`), voice/receipt prompts and snapping, preset columns and the
symbol ↔ ISO maps in `sheet_data` are all derived from `CURRENCIES`. Adding a
Currency is one line there.

``format_amount`` is the one way the bot shows an amount: «1 234 дин».
``label`` is the one way a Currency button looks: «🇷🇸 дин», «🇨🇳 CNY».
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


# Setting that names the household's base Currency (ISO code): the bot uses it
# when a voice/receipt/typed expense names no Currency, the dashboard converts
# every amount into it. Must be one of CURRENCIES for the bot.
BASE_CURRENCY_ENV = "BASE_CURRENCY"
# Base Currency of a household that has not set BASE_CURRENCY.
DEFAULT_BASE = next(c for c in CURRENCIES if c.code == "EUR")


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


def stored_value(code: str) -> str:
    """Value written to Data for an ISO code: the legacy symbol, else the code (Q8)."""
    upper = code.strip().upper()
    found = next((c for c in CURRENCIES if c.code == upper), None)
    return found.symbol if found is not None else upper


# Regional indicator symbol for "A"; two of them in a row show as a flag.
REGIONAL_A = 0x1F1E6
# Codes that start with X belong to no country (XAF, XDR, XAU …).
NO_COUNTRY_FLAG = "🌐"
# Codes whose first two letters are not a current country: ANG (Netherlands
# Antilles guilder) is used in Curaçao.
FLAG_COUNTRY = {"ANG": "CW"}


def flag(code: str) -> str:
    """
    Flag emoji of an ISO 4217 code: its first two letters are the country (EU for EUR).

    Parameters
    ----------
    code : str
        ISO 4217 code, any case.

    Returns
    -------
    str
        Flag emoji, or 🌐 for a code with no country.
    """
    upper = code.strip().upper()
    if len(upper) != 3 or not (upper.isascii() and upper.isalpha()) or upper[0] == "X":
        return NO_COUNTRY_FLAG
    country = FLAG_COUNTRY.get(upper, upper[:2])
    return "".join(chr(REGIONAL_A + ord(ch) - ord("A")) for ch in country)


def label(value: str) -> str:
    """Button text for a stored value or ISO code: flag and stored value, «🇷🇸 дин»."""
    found = find(value)
    shown = found.symbol if found is not None else stored_value(value)
    return f"{flag(to_iso(shown))} {shown}"


def to_iso(value: str) -> str:
    """ISO code for a stored symbol or code; an unknown 3-letter value is upper-cased."""
    found = find(value)
    if found is not None:
        return found.code
    return value.upper() if len(value) == 3 else value


CENT = Decimal("0.01")


def format_amount(amount: float, symbol: str | None = None) -> str:
    """
    Amount as the bot shows it: «1 234 дин», «12,50 €».

    Thousands are split by a space, the decimal mark is a comma, and the two
    decimals appear only when they are not zero. Rounds half up to cents.

    Parameters
    ----------
    amount : float
        Amount to show.
    symbol : str or None
        Currency symbol put after the number; None shows the number alone.

    Returns
    -------
    str
        Formatted amount.
    """
    cents = int(Decimal(str(amount)).quantize(CENT, rounding=ROUND_HALF_UP) * 100)
    whole, frac = divmod(abs(cents), 100)
    text = ("-" if cents < 0 else "") + f"{whole:,}".replace(",", " ")
    if frac:
        text += f",{frac:02d}"
    return f"{text} {symbol}" if symbol else text
