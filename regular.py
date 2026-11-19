#!/usr/bin/env python3
"""Presets: parse the 'Regular_expenses' sheet.

The sheet is human-friendly (one row per item; the amount lives in a
per-currency column) and split into two sections by divider rows:

    ежемесячные траты | динары | евро | доллары | рубли | категория | кто
    Rent              |        | 850  |         |       | Rent       | Both
    ...
    разовые траты
    баня              | 3850   |      |         |       | Beauty ... | Both

Both sections are surfaced in the bot as one-tap preset buttons; the
only difference is the label section. This module just reads and parses the
sheet — no posting logic here.
"""
from __future__ import annotations

from typing import Optional

from currency import find as find_currency
from sheets import SheetsClient, open_client
from sheet_data import parse_amount


def _find_col(header: list[str], substr: str) -> Optional[int]:
    for i, h in enumerate(header):
        if substr in str(h).strip().lower():
            return i
    return None


def parse_regular(values: list[list[str]]) -> dict:
    """Split the sheet grid into {'monthly': [...], 'oneoff': [...]}.

    Each item: {name, amount, currency, category, who}. Rows without an amount
    (e.g. a subscription with a blank price) or that are section dividers are
    skipped.
    """
    if not values:
        return {"monthly": [], "oneoff": []}
    header = values[0]
    # A header word the currency registry recognises ("доллары", "€", "CNY")
    # makes that column a Currency column. Whole words only, so "клиенты"
    # is not read as yen. The first column wins per Currency.
    cur_cols: dict[int, str] = {}
    for i, h in enumerate(header):
        for word in str(h).strip().split():
            found = find_currency(word)
            if found is not None and found.symbol not in cur_cols.values():
                cur_cols[i] = found.symbol
                break
    cat_col = _find_col(header, "категор")
    who_col = _find_col(header, "кто")

    section = "monthly"
    result: dict[str, list[dict]] = {"monthly": [], "oneoff": []}
    for row in values[1:]:
        name = (row[0].strip() if row and row[0] else "")
        if not name:
            continue
        low = name.lower()
        if "разов" in low:
            section = "oneoff"
            continue
        if "ежемес" in low:
            section = "monthly"
            continue
        amount = currency = None
        for idx, sym in cur_cols.items():
            if idx < len(row) and str(row[idx]).strip():
                value = parse_amount(row[idx])
                if value is not None:
                    amount, currency = value, sym
                    break
        if amount is None:
            continue
        category = (row[cat_col].strip() if cat_col is not None and cat_col < len(row)
                    else "") or "Other"
        who = (row[who_col].strip() if who_col is not None and who_col < len(row)
               else "") or "Both"
        result[section].append({
            "name": name, "amount": amount, "currency": currency,
            "category": category, "who": who,
        })
    return result


def load_presets(client: SheetsClient | None = None) -> list[dict]:
    """Return all presets (monthly first, then one-off) as a flat list.

    Each item carries a 'section' key ('monthly'|'oneoff') for optional grouping.
    """
    sheets = client or open_client()
    values = sheets.read_regular_values()
    parsed = parse_regular(values)
    items = []
    for section in ("monthly", "oneoff"):
        for it in parsed[section]:
            items.append({**it, "section": section})
    return items
