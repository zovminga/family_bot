"""Pytest configuration: keep repo root on sys.path, isolate from secrets.

Importing bot.py loads .env and reaches Google Sheets at import time. A checkout
that holds a real .env (for example the copy the dashboard launchd job uses)
would then talk to the live spreadsheet. Neutralise both before any test module
imports bot.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import dotenv

dotenv.load_dotenv = lambda *args, **kwargs: False
for _var in ("GOOGLE_CREDS_PATH", "SHEET_NAME", "BOT_TOKEN", "ANTHROPIC_API_KEY",
             "DASHBOARD_CHAT_ID"):
    os.environ.pop(_var, None)
os.environ["ALLOWED_USERS"] = "111:Azat,222:Lisa"

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
