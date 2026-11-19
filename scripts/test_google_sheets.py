#!/usr/bin/env python3
"""Manual smoke script: Google Sheets connection via SheetsClient.

Not part of pytest — run from repo root:
  python3 scripts/test_google_sheets.py

Reads .env from the repo root first, like the bot.
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
sys.path.insert(0, str(ROOT))

from family_bot import logs
from family_bot.sheets import open_client


def main():
    load_dotenv(ENV_FILE)
    # print() below is this check's report to the person running it; library
    # messages (e.g. the Sheets connection error) go through logging.
    logs.setup()
    print("🔍 Testing Google Sheets connection...")
    print("=" * 50)

    print("📋 Environment variables:")
    creds_path = os.getenv("GOOGLE_CREDS_PATH")
    sheet_name = os.getenv("SHEET_NAME")

    if creds_path:
        print(f"✅ GOOGLE_CREDS_PATH: {creds_path}")
        if os.path.exists(creds_path):
            print("✅ Credentials file found")
        else:
            print("❌ Credentials file not found")
    else:
        print("❌ GOOGLE_CREDS_PATH is not set")

    if sheet_name:
        print(f"✅ SHEET_NAME: {sheet_name}")
    else:
        print("❌ SHEET_NAME is not set")

    print("\n🔗 Connection test:")

    try:
        client = open_client()
    except Exception as e:
        print(f"❌ Could not open SheetsClient: {e}")
        print("\n💡 Possible causes:")
        print("1. Wrong credentials path")
        print("2. Wrong spreadsheet name")
        print("3. Access permissions")
        print("4. Network issues")
        return

    if not client.test_connection():
        print("❌ Could not connect to Google Sheets")
        return

    print("✅ Connected to Google Sheets!")

    print("\n📋 Loading categories:")
    try:
        categories = client.read_categories()
        if categories:
            print(f"✅ Loaded {len(categories)} categories:")
            for i, cat in enumerate(categories, 1):
                print(f"  {i}. {cat}")
        else:
            print("⚠️  No categories found in Config sheet")
    except Exception as e:
        print(f"❌ Error loading categories: {e}")

    print("\n📊 Testing Data sheet:")
    try:
        rows = client.read_data_values()
        print("✅ Data sheet accessible")
        if rows:
            print(f"✅ Found {len(rows)} rows")
            if len(rows) > 1:
                print("📋 Column headers:")
                for i, header in enumerate(rows[0], 1):
                    print(f"  {i}. {header}")
        else:
            print("⚠️  Data sheet is empty")
    except Exception as e:
        print(f"❌ Error accessing Data sheet: {e}")


if __name__ == "__main__":
    main()
