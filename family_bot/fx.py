"""Exchange rates for the bot and the dashboard (exchangerate-api.com, no key).

Blocking HTTP: bot callers run it in ``asyncio.to_thread``.

Statistics Conversion reads one rate table per target Currency (``RATES``):
all source Currencies of one request come from that single response, and the
table is reused for ``RATE_TTL_SEC`` — Conversion uses the rate of the day the
statistics are requested (Q7), so an hour-old table is close enough.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional

import requests

from family_bot import errors
from family_bot.currency import to_iso


RATES_URL = "https://api.exchangerate-api.com/v4/latest/{base}"
# Dashboard build reads the whole table once.
TIMEOUT_SEC = 10.0
# A bot user waits for the stats answer, so a pair lookup gives up sooner.
RATE_TIMEOUT_SEC = 5.0
# Statistics Conversion reuses a rate table this long (rate of the day, Q7).
RATE_TTL_SEC = 3600.0
# A failed read is not repeated for the other Currencies of the same request.
RETRY_AFTER_FAILURE_SEC = 60.0


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


class RateCache:
    """
    Rate tables by base Currency, cached; a failed read is remembered briefly.

    Parameters
    ----------
    fetch : Optional[Callable[[str], dict[str, float]]]
        Reads ``{ISO: units per 1 base}`` for a base ISO code; default
        ``fetch_rates`` with ``RATE_TIMEOUT_SEC`` (network).
    clock : Callable[[], float]
        Monotonic seconds, for the cache age.
    """

    def __init__(self, fetch: Optional[Callable[[str], dict[str, float]]] = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._fetch = fetch or (lambda base: fetch_rates(base, timeout=RATE_TIMEOUT_SEC))
        self._clock = clock
        self._tables: dict[str, tuple[float, Optional[dict[str, float]]]] = {}
        # One read per base even when two Users ask at the same moment; a slow
        # read for one base does not hold up the others.
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def table(self, base_iso: str) -> Optional[dict[str, float]]:
        """``{ISO: units per 1 base}``, or None when the service failed."""
        with self._locks_guard:
            lock = self._locks.setdefault(base_iso, threading.Lock())
        with lock:
            cached = self._tables.get(base_iso)
            if cached is not None:
                at, table = cached
                ttl = RATE_TTL_SEC if table is not None else RETRY_AFTER_FAILURE_SEC
                if self._clock() - at < ttl:
                    return table
            try:
                fresh: Optional[dict[str, float]] = self._fetch(base_iso)
            except (requests.RequestException, ValueError) as e:
                errors.log_exception("Exchange rates", e)
                fresh = None
            self._tables[base_iso] = (self._clock(), fresh)
            return fresh


RATES = RateCache()


def rate(from_currency: str, to_currency: str) -> Optional[float]:
    """
    How many ``to_currency`` units one ``from_currency`` unit buys.

    Reads the cached table for ``to_currency`` (``RATES``), so every source
    Currency of one statistics request shares one call to the service.

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
    if to_iso(from_currency) == to_iso(to_currency):
        return 1.0
    table = RATES.table(to_iso(to_currency))
    value = table.get(to_iso(from_currency)) if table else None
    return 1.0 / float(value) if value else None


# Any base works: the service lists the same currencies for each.
CODES_BASE = "EUR"


def currency_codes() -> list[str]:
    """
    ISO codes of every currency the rate service knows.

    Returns
    -------
    list[str]
        Codes as the service lists them.

    Raises
    ------
    requests.RequestException
        The request failed or returned an HTTP error.
    """
    return list(fetch_rates(CODES_BASE))
