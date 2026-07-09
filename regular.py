#!/usr/bin/env python3
"""Default (preset) expenses: parse the 'Regular_expenses' sheet.

The sheet is human-friendly (one row per item; the amount lives in a
per-currency column) and split into two sections by divider rows:

    ежемесячные траты | динары | евро | доллары | рубли | категория | кто
    Rent              |        | 850  |         |       | Rent       | Both
    ...
    разовые траты
    баня              | 3850   |      |         |       | Beauty ... | Both

Both sections are surfaced in the bot as one-tap «default expense» buttons; the
only difference is the label section. This module just reads and parses the
sheet — no posting logic here.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

BOT_DIR = Path(__file__).resolve().parent

# Currency column header (substring) -> symbol used in the Data sheet.
CURRENCY_WORDS = [("динар", "дин"), ("евро", "€"), ("доллар", "$"), ("рубл", "₽")]

REGULAR_SHEET = "Regular_expenses"


def _open_spreadsheet():
    import gspread
    from oauth2client.service_account import ServiceAccountCredentials

    scope = ["https://www.googleapis.com/auth/drive",
             "https://www.googleapis.com/auth/spreadsheets"]
    creds_path = os.getenv("GOOGLE_CREDS_PATH")
    if not creds_path:
        raise RuntimeError("GOOGLE_CREDS_PATH is not set.")
    creds_file = Path(creds_path)
    if not creds_file.is_absolute():
        creds_file = (BOT_DIR / creds_file).resolve()
    sheet_name = os.getenv("SHEET_NAME")
    if not sheet_name:
        raise RuntimeError("SHEET_NAME is not set.")
    creds = ServiceAccountCredentials.from_json_keyfile_name(str(creds_file), scope)
    return gspread.authorize(creds).open(sheet_name)


def _num(raw) -> Optional[float]:
    text = str(raw).strip().replace(" ", "").replace("\u00a0", "")
    if not text:
        return None
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


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
    cur_cols = {i: sym for word, sym in CURRENCY_WORDS
                for i in [_find_col(header, word)] if i is not None}
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
                value = _num(row[idx])
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


def load_defaults() -> list[dict]:
    """Return all preset expenses (monthly first, then one-off) as a flat list.

    Each item carries a 'section' key ('monthly'|'oneoff') for optional grouping.
    """
    values = _open_spreadsheet().worksheet(REGULAR_SHEET).get_all_values()
    parsed = parse_regular(values)
    items = []
    for section in ("monthly", "oneoff"):
        for it in parsed[section]:
            items.append({**it, "section": section})
    return items
