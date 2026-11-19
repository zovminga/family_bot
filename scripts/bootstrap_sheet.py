#!/usr/bin/env python3
"""Bootstrap a fresh Google spreadsheet for a new family_bot install.

Prerequisites:
  1. Service account JSON + GOOGLE_CREDS_PATH
  2. Empty (or existing) spreadsheet named SHEET_NAME, shared with the
     service account email as Editor

Creates worksheets Data / Config / Regular_expenses / Meta with headers
and default categories. Safe to re-run — does not wipe existing data.

Usage:
  python scripts/bootstrap_sheet.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

BOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BOT_DIR))
load_dotenv(BOT_DIR / ".env")

from family_bot.sheets import open_client  # noqa: E402


def main() -> int:
    creds = os.getenv("GOOGLE_CREDS_PATH")
    sheet = os.getenv("SHEET_NAME")
    if not creds or not sheet:
        print("❌ Set GOOGLE_CREDS_PATH and SHEET_NAME in .env first.")
        return 1

    print(f"→ Opening spreadsheet {sheet!r} …")
    client = open_client()
    changed = client.ensure_sheets()
    url = client.url or "(url unavailable)"

    if not any(changed.values()):
        print(f"✅ Sheets already ready.\n   {url}")
        return 0

    print("✅ Bootstrap done:")
    for key, did in changed.items():
        if did:
            print(f"   • {key}")
    print(f"   {url}")
    print(
        "\nNext: set ALLOWED_USERS in .env (use /whoami in Telegram), "
        "then run: family-bot"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
