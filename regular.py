#!/usr/bin/env python3
"""Regular expenses: parse the 'Regular_expenses' sheet and post monthly ones.

The sheet is human-friendly (one row per item; the amount lives in a
per-currency column) and split into two sections by divider rows:

    ежемесячные траты | динары | евро | доллары | рубли | категория | кто
    Rent              |        | 850  |         |       | Rent       | Both
    ...
    разовые траты
    баня              | 3850   |      |         |       | Beauty ... | Both

- Rows under «ежемесячные траты» are auto-posted to Data on the 1st of a month.
- Rows under «разовые траты» power the quick-template buttons in the bot.

This module is self-contained (its own gspread auth) so the monthly launchd
script can use it without importing the Telegram bot.
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Optional

BOT_DIR = Path(__file__).resolve().parent

# Currency column header (substring) -> symbol used in the Data sheet.
CURRENCY_WORDS = [("динар", "дин"), ("евро", "€"), ("доллар", "$"), ("рубл", "₽")]

REGULAR_SHEET = "Regular_expenses"
DATA_SHEET = "Data"
META_SHEET = "Meta"
META_MONTH_CELL = "B3"       # stores the last month regular expenses were posted
META_MONTH_LABEL_CELL = "A3"


# ---------------------------------------------------------------------------
# Google Sheets access (self-contained)
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
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


def load_regular() -> dict:
    """Read and parse the Regular_expenses sheet from Google Sheets."""
    sp = _open_spreadsheet()
    values = sp.worksheet(REGULAR_SHEET).get_all_values()
    return parse_regular(values)


# ---------------------------------------------------------------------------
# Monthly posting (idempotent)
# ---------------------------------------------------------------------------
def _get_or_create_meta(sp):
    import gspread
    try:
        return sp.worksheet(META_SHEET)
    except gspread.WorksheetNotFound:
        return sp.add_worksheet(title=META_SHEET, rows=10, cols=2)


def append_monthly(force: bool = False) -> dict:
    """Append every «ежемесячные» expense to Data with date = 1st of this month.

    Idempotent: a month is posted at most once (guarded by a cell in Meta),
    unless force=True. Returns {'added': [names], 'skipped': reason_or_None}.
    """
    sp = _open_spreadsheet()
    monthly = parse_regular(sp.worksheet(REGULAR_SHEET).get_all_values())["monthly"]
    if not monthly:
        return {"added": [], "skipped": "no monthly expenses found"}

    now = datetime.now()
    month = now.strftime("%Y-%m")
    first_day = now.replace(day=1).strftime("%d.%m.%Y")

    meta = _get_or_create_meta(sp)
    if not force and (meta.acell(META_MONTH_CELL).value or "").strip() == month:
        return {"added": [], "skipped": f"already posted for {month}"}

    rows = [[first_day, month, it["category"], it["amount"], it["currency"],
             it["who"], it["name"]] for it in monthly]
    sp.worksheet(DATA_SHEET).append_rows(rows, value_input_option="USER_ENTERED")

    meta.update_acell(META_MONTH_LABEL_CELL, "regular_last_month")
    meta.update_acell(META_MONTH_CELL, month)
    return {"added": [it["name"] for it in monthly], "skipped": None}


if __name__ == "__main__":
    import sys
    from dotenv import load_dotenv

    load_dotenv(BOT_DIR / ".env")
    forced = "--force" in sys.argv[1:]
    outcome = append_monthly(force=forced)
    if outcome["skipped"]:
        print(f"↷ Skipped: {outcome['skipped']}")
    else:
        print(f"✅ Posted {len(outcome['added'])} monthly expense(s): "
              f"{', '.join(outcome['added'])}")
