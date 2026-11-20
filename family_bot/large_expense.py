#!/usr/bin/env python3
"""Large expense («Крупная трата»): whether to suggest the mark (no Telegram).

A Large expense is a big one-off spend that routine statistics skip. The bot
suggests the mark when the amount, converted to the base Currency, is above
the threshold (``LARGE_EXPENSE_THRESHOLD``); a person confirms it. How the
mark is stored in Data lives in ``sheet_data`` (cell) and ``expense`` (write).
"""
from __future__ import annotations

from typing import Callable, Optional

from family_bot.currency import to_iso

GetRate = Callable[[str, str], Optional[float]]

THRESHOLD_ENV = "LARGE_EXPENSE_THRESHOLD"
# Threshold in the base Currency when the setting is unset or wrong.
DEFAULT_THRESHOLD = 1000.0


def suggest(amount: float, currency: str, *, threshold: float, base: str,
            get_rate: GetRate) -> bool:
    """
    True when the Expense is above the threshold in the base Currency.

    Parameters
    ----------
    amount : float
        Expense amount in ``currency``.
    currency : str
        Stored symbol or ISO code of the Expense.
    threshold : float
        Threshold in ``base``; an amount equal to it is not above it.
    base : str
        Base Currency (``BASE_CURRENCY``), stored symbol or ISO code.
    get_rate : GetRate
        ``fx.rate``: units of ``base`` per one ``currency``, None when unknown.

    Returns
    -------
    bool
        False when there is no rate: the mark is then not suggested.
    """
    if to_iso(currency) == to_iso(base):
        return amount > threshold
    rate = get_rate(currency, base)
    return rate is not None and amount * rate > threshold
