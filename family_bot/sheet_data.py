#!/usr/bin/env python3
"""Shared parsing kernel for Data-sheet rows.

One locality for amount parsing, header aliases, month keys, the typed ``Row``
and the currency symbol ↔ ISO maps (derived from currency.py) used by the bot
and the dashboard.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Optional

from family_bot import currency

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

# Stored currency value → ISO code, derived from the registry in currency.py.
SYMBOL_TO_ISO: dict[str, str] = {c.symbol: c.code for c in currency.CURRENCIES}
ISO_TO_SYMBOL: dict[str, str] = {iso: sym for sym, iso in SYMBOL_TO_ISO.items()}

DATE_FMT = "%d.%m.%Y"
MONTH_FMT = "%Y-%m"
# Date cell formats read from the sheet: the bot writes DATE_FMT, the rest are hand edits.
READ_DATE_FMTS = (DATE_FMT, "%Y-%m-%d", "%d/%m/%Y")


@dataclass(frozen=True)
class Row:
    """
    One Data-sheet expense with typed fields.

    Parameters
    ----------
    date : Optional[date]
        Date cell; None when it is empty or not in a known format.
    month : str
        ``YYYY-MM`` from the Month cell, or derived from the date; "" when neither.
    category : str
        Category cell as stored.
    amount : float
        Amount cell parsed by ``parse_amount``.
    currency : str
        Currency cell as stored (symbol such as «дин», or an ISO code).
    who : str
        Spender cell as stored.
    comment : str
        Comment cell as stored.
    """

    date: Optional[date]
    month: str
    category: str
    amount: float
    currency: str
    who: str
    comment: str

    @property
    def date_text(self) -> str:
        """Date in ``DATE_FMT``, or "" when the row has no date."""
        return self.date.strftime(DATE_FMT) if self.date else ""


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
        .replace("\u202f", "")
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


def parse_date(raw: str) -> Optional[date]:
    """Parse a Date cell in one of ``READ_DATE_FMTS``; None when it does not match."""
    text = str(raw).strip()
    for fmt in READ_DATE_FMTS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def normalize_month(date_str: str, month_str: str = "") -> str:
    """Return a YYYY-MM month key, deriving it from the date when needed."""
    if month_str and len(month_str.strip()) >= 7:
        return month_str.strip()[:7]
    parsed = parse_date(date_str) if date_str else None
    return parsed.strftime(MONTH_FMT) if parsed else ""


def cell(row: list, columns: dict[str, int], field: str) -> str:
    idx = columns.get(field)
    if idx is None or idx >= len(row):
        return ""
    return str(row[idx]).strip()


def parse_row(row: list, columns: dict[str, int]) -> Optional[Row]:
    """Parse one Data-sheet row, or None when the amount is missing or not a number."""
    if "amount" not in columns:
        return None
    idx = columns["amount"]
    amount = parse_amount(row[idx] if idx < len(row) else "")
    if amount is None:
        return None
    date_cell = cell(row, columns, "date")
    return Row(
        date=parse_date(date_cell),
        month=normalize_month(date_cell, cell(row, columns, "month")),
        category=cell(row, columns, "category"),
        amount=amount,
        currency=cell(row, columns, "currency"),
        who=cell(row, columns, "who"),
        comment=cell(row, columns, "comment"),
    )


def parse_rows(headers: list[str], raw_rows: list[list]) -> list[Row]:
    """Parse a full Data sheet (headers + body rows). Skips rows without a numeric amount."""
    columns = resolve_columns(headers)
    rows = (parse_row(raw, columns) for raw in raw_rows)
    return [r for r in rows if r is not None]
