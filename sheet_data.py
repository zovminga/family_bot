#!/usr/bin/env python3
"""Shared parsing kernel for Data-sheet rows.

One locality for amount parsing, header aliases, month keys, and currency
symbol ↔ ISO maps used by the bot, dashboard, presets, and voice snaps.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

# Header aliases (English + Russian), matched case-insensitively.
HEADER_ALIASES: dict[str, list[str]] = {
    "date": ["date", "дата"],
    "month": ["month", "месяц"],
    "category": ["category", "категория"],
    "amount": ["amount", "сумма"],
    "currency": ["currency", "валюта"],
    "who": ["who", "кто внес", "spender", "кто"],
    "comment": ["comment", "комментарий", "коммент"],
}

# Currency symbols used in the sheet → ISO codes.
SYMBOL_TO_ISO: dict[str, str] = {
    "₽": "RUB",
    "дин": "RSD",
    "€": "EUR",
    "¥": "JPY",
    "$": "USD",
    "£": "GBP",
}
ISO_TO_SYMBOL: dict[str, str] = {iso: sym for sym, iso in SYMBOL_TO_ISO.items()}

DATE_FMT = "%d.%m.%Y"
MONTH_FMT = "%Y-%m"


def resolve_columns(headers: list[str]) -> dict[str, int]:
    """Map logical field names to column indices using HEADER_ALIASES."""
    lowered = [str(h).strip().lower() for h in headers]
    columns: dict[str, int] = {}
    for field, aliases in HEADER_ALIASES.items():
        for alias in aliases:
            if alias in lowered:
                columns[field] = lowered.index(alias)
                break
    return columns


def parse_amount(raw: Any) -> Optional[float]:
    """Parse an amount cell, tolerating comma decimals and thousands separators.

    Separator rule: a dot alone is a decimal point ("12.5"). A single comma
    followed by 1-2 digits is a decimal comma ("12,5", "7,65"); any other comma
    is a thousands separator ("1,234", "12,500", "1,234,567"). With both a dot
    and a comma, the rightmost one is the decimal point ("1.234,56", "1,234.56").
    """
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip()
    if not text:
        return None
    text = (
        text.replace(" ", "")
        .replace("\u00a0", "")
        .replace("'", "")
    )
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        parts = text.split(",")
        if len(parts) == 2 and 1 <= len(parts[1]) <= 2 and parts[1].isdigit():
            text = text.replace(",", ".")
        else:
            text = text.replace(",", "")
    try:
        return float(text)
    except ValueError:
        return None


def normalize_month(date_str: str, month_str: str = "") -> str:
    """Return a YYYY-MM month key, deriving it from the date when needed."""
    if month_str and len(month_str.strip()) >= 7:
        return month_str.strip()[:7]
    if not date_str:
        return ""
    for fmt in (DATE_FMT, "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(str(date_str).strip(), fmt).strftime(MONTH_FMT)
        except (ValueError, AttributeError):
            continue
    return ""


def cell(row: list, columns: dict[str, int], field: str) -> str:
    idx = columns.get(field)
    if idx is None or idx >= len(row):
        return ""
    return str(row[idx]).strip()


def normalize_row(row: list, columns: dict[str, int]) -> Optional[dict]:
    """Normalize one Data-sheet row into a dict, or None if amount is missing."""
    if "amount" not in columns:
        return None
    idx = columns["amount"]
    amount = parse_amount(row[idx] if idx < len(row) else "")
    if amount is None:
        return None
    date = cell(row, columns, "date")
    month = normalize_month(date, cell(row, columns, "month"))
    return {
        "date": date,
        "month": month,
        "category": cell(row, columns, "category"),
        "amount": amount,
        "currency": cell(row, columns, "currency"),
        "who": cell(row, columns, "who"),
        "comment": cell(row, columns, "comment"),
    }


def normalize_rows(headers: list[str], raw_rows: list[list]) -> list[dict]:
    """Normalize a full Data sheet (headers + body rows). Skips unparseable amounts."""
    columns = resolve_columns(headers)
    if "amount" not in columns:
        return []
    out: list[dict] = []
    for row in raw_rows:
        rec = normalize_row(row, columns)
        if rec is not None:
            out.append(rec)
    return out


def to_iso(currency: str) -> str:
    """Map a sheet currency symbol (or ISO) to an ISO code."""
    if not currency:
        return currency
    return SYMBOL_TO_ISO.get(currency, currency.upper() if len(currency) == 3 else currency)
