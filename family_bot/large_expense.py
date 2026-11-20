#!/usr/bin/env python3
"""Large expense («Крупная трата»): whether to suggest the mark (no Telegram).

A Large expense is a big one-off spend that routine statistics skip. The bot
suggests the mark when the amount, converted to the base Currency, is above
the threshold (``LARGE_EXPENSE_THRESHOLD``); a person confirms it. The
threshold has its own Currency (1000 € unless the setting names another), not
the base Currency: with ``BASE_CURRENCY=RSD`` a bare 1000 would be 1000 дин. How the
mark is stored in Data lives in ``sheet_data`` (cell) and ``expense`` (write).
"""
from __future__ import annotations

from typing import Callable, Optional

from family_bot.currency import to_iso

GetRate = Callable[[str, str], Optional[float]]

THRESHOLD_ENV = "LARGE_EXPENSE_THRESHOLD"
# Threshold when the setting is unset or wrong, and its Currency (stored symbol),
# also used when the setting gives a number without a Currency.
DEFAULT_THRESHOLD = 1000.0
DEFAULT_THRESHOLD_CURRENCY = "€"


def suggest(amount: float, currency: str, *, threshold: float, threshold_currency: str,
            get_rate: GetRate) -> bool:
    """
    True when the Expense, converted to the threshold Currency, is above it.

    Parameters
    ----------
    amount : float
        Expense amount in ``currency``.
    currency : str
        Stored symbol or ISO code of the Expense.
    threshold : float
        Threshold in ``threshold_currency``; an amount equal to it is not above it.
    threshold_currency : str
        Currency of the threshold, stored symbol or ISO code.
    get_rate : GetRate
        ``fx.rate``: units of ``threshold_currency`` per one ``currency``,
        None when unknown.

    Returns
    -------
    bool
        False when there is no rate: the mark is then not suggested.
    """
    if to_iso(currency) == to_iso(threshold_currency):
        return amount > threshold
    rate = get_rate(currency, threshold_currency)
    return rate is not None and amount * rate > threshold
