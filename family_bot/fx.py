"""Exchange rates for the bot and the dashboard (exchangerate-api.com, no key).

Blocking HTTP: bot callers run it in ``asyncio.to_thread``.
"""
from __future__ import annotations

from typing import Optional

import requests

from family_bot.currency import to_iso

RATES_URL = "https://api.exchangerate-api.com/v4/latest/{base}"
# Dashboard build reads the whole table once.
TIMEOUT_SEC = 10.0
# A bot user waits for the stats answer, so a pair lookup gives up sooner.
RATE_TIMEOUT_SEC = 5.0


def fetch_rates(base_iso: str, timeout: float = TIMEOUT_SEC) -> dict[str, float]:
    """
    Today's rates against one base currency.

    Parameters
    ----------
    base_iso : str
        ISO code of the base currency.
    timeout : float
        HTTP timeout in seconds.

    Returns
    -------
    dict[str, float]
        ``{ISO: units of ISO per 1 base}``; the base itself maps to 1.0.

    Raises
    ------
    requests.RequestException
        The request failed or returned an HTTP error.
    """
    resp = requests.get(RATES_URL.format(base=base_iso), timeout=timeout)
    resp.raise_for_status()
    rates: dict[str, float] = resp.json().get("rates", {})
    rates[base_iso] = 1.0
    return rates


def rate(from_currency: str, to_currency: str) -> Optional[float]:
    """
    How many ``to_currency`` units one ``from_currency`` unit buys.

    Parameters
    ----------
    from_currency : str
        Stored symbol or ISO code.
    to_currency : str
        Stored symbol or ISO code.

    Returns
    -------
    Optional[float]
        The rate, or None when the request failed or the currency has no rate.
    """
    if from_currency == to_currency:
        return 1.0
    try:
        rates = fetch_rates(to_iso(from_currency), timeout=RATE_TIMEOUT_SEC)
    except (requests.RequestException, ValueError) as e:
        print(f"Error getting exchange rate: {e}")
        return None
    value = rates.get(to_iso(to_currency))
    return float(value) if value else None
