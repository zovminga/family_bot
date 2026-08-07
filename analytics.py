#!/usr/bin/env python3
"""Expense analytics over normalized rows (no Sheets I/O).

Callers load rows via SheetsClient + sheet_data.normalize_rows, then pass them
here. Exchange rates come through an injectable get_rate adapter.
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable, Optional

import requests

from sheet_data import DATE_FMT, SYMBOL_TO_ISO

GetRate = Callable[[str, str], Optional[float]]


def http_exchange_rate(from_currency: str, to_currency: str) -> Optional[float]:
    """Fetch a rate via exchangerate-api.com (production Fx adapter)."""
    try:
        if from_currency == to_currency:
            return 1.0
        from_cur = SYMBOL_TO_ISO.get(from_currency, from_currency)
        to_cur = SYMBOL_TO_ISO.get(to_currency, to_currency)
        url = f"https://api.exchangerate-api.com/v4/latest/{from_cur}"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            rate = response.json().get("rates", {}).get(to_cur)
            if rate:
                return float(rate)
        return None
    except Exception as e:  # noqa: BLE001 - degrade to None for callers
        print(f"Error getting exchange rate: {e}")
        return None


def unique_currencies(rows: list[dict], fallback: Optional[list[str]] = None) -> list[str]:
    """Sorted distinct currency symbols from normalized rows."""
    currencies = sorted({r.get("currency") for r in rows if r.get("currency")})
    if currencies:
        return currencies
    return list(fallback or [])


def last_n(
    rows: list[dict],
    n: int = 3,
    category: Optional[str] = None,
) -> list[dict]:
    """Return the last N expenses (newest first), optionally filtered by category."""
    filtered = rows
    if category and category != "All":
        filtered = [r for r in rows if r.get("category") == category]
    if not filtered:
        return []
    return list(reversed(filtered[-n:]))


def format_last_n(
    rows: list[dict],
    n: int = 3,
    category: Optional[str] = None,
) -> str:
    """Human-readable last-N listing for Telegram."""
    records = last_n(rows, n=n, category=category)
    if not records:
        if category and category != "All":
            return f"📭 No records for category: {category}"
        return "📭 No records"

    lines = [f"📋 Last {n} records:\n"]
    for i, record in enumerate(records, 1):
        amount = record["amount"]
        comment = record.get("comment") or ""
        comment_text = f" ({comment})" if comment else ""
        lines.append(
            f"{i}. 📅 {record.get('date') or '?'} | {record.get('category') or '?'} | "
            f"{amount:,.2f} {record.get('currency') or '?'} | "
            f"👤 {record.get('who') or '?'}{comment_text}"
        )
    return "\n".join(lines)


def _parse_date(value: str) -> Optional[datetime]:
    try:
        return datetime.strptime(value, DATE_FMT)
    except (TypeError, ValueError):
        return None


def filter_rows(
    rows: list[dict],
    *,
    category: str = "All",
    month: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> list[dict]:
    """Filter normalized expenses by category and period."""
    out = list(rows)
    if month:
        out = [r for r in out if r.get("month") == month]
    elif date_from and date_to:
        start = _parse_date(date_from)
        end = _parse_date(date_to)
        if start is None or end is None:
            return []
        filtered = []
        for r in out:
            d = _parse_date(r.get("date") or "")
            if d is not None and start <= d <= end:
                filtered.append(r)
        out = filtered
    if category != "All":
        out = [r for r in out if r.get("category") == category]
    return out


def aggregate(
    rows: list[dict],
    *,
    category: str = "All",
    month: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    group_by_currency: bool = True,
    convert_to_currency: Optional[str] = None,
    get_rate: Optional[GetRate] = None,
) -> tuple[str, dict]:
    """Aggregate filtered expenses into Telegram text + conversion details.

    get_rate(from_currency, to_currency) -> rate | None. Defaults to http_exchange_rate.
    """
    get_rate = get_rate or http_exchange_rate
    conversion_details: dict = {}

    filtered = filter_rows(
        rows,
        category=category,
        month=month,
        date_from=date_from,
        date_to=date_to,
    )
    if not filtered:
        return "No data 🤷", {}

    # Working copy of amounts/currencies for optional conversion.
    working = [
        {"amount": float(r["amount"]), "currency": r.get("currency") or ""}
        for r in filtered
    ]

    if convert_to_currency:
        originals = {w["currency"] for w in working}
        for currency in originals:
            if currency == convert_to_currency:
                continue
            rate = get_rate(currency, convert_to_currency)
            if rate is None:
                return (
                    f"❌ Failed to get exchange rate for {currency} → {convert_to_currency}",
                    {},
                )
            original_amount = sum(w["amount"] for w in working if w["currency"] == currency)
            for w in working:
                if w["currency"] == currency:
                    w["amount"] *= rate
                    w["currency"] = convert_to_currency
            conversion_details[currency] = {
                "rate": rate,
                "original_amount": original_amount,
                "converted_amount": original_amount * rate,
            }

    if group_by_currency:
        totals: dict[str, float] = {}
        for w in working:
            totals[w["currency"]] = totals.get(w["currency"], 0.0) + w["amount"]
        lines = [f"{cur}: {amt:,.2f}" for cur, amt in totals.items()]
        return ("\n".join(lines) if lines else "No data 🤷"), conversion_details

    total = sum(w["amount"] for w in working)
    currency = (
        convert_to_currency
        if convert_to_currency
        else (working[0]["currency"] if working else "?")
    )
    return f"💰 Total: {total:,.2f} {currency}", conversion_details
