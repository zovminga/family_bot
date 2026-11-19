"""Isolation for known-bug reproduction tests.

Importing bot.py loads .env and reaches Google Sheets at import time. A checkout
that holds a real .env (for example the copy the dashboard launchd job uses)
would then talk to the live spreadsheet. Neutralise both before any test module
imports bot.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import dotenv
import pytest

dotenv.load_dotenv = lambda *args, **kwargs: False
for _var in ("GOOGLE_CREDS_PATH", "SHEET_NAME", "BOT_TOKEN", "ANTHROPIC_API_KEY",
             "DASHBOARD_CHAT_ID"):
    os.environ.pop(_var, None)
os.environ["ALLOWED_USERS"] = "111:Azat,222:Lisa"


@pytest.fixture
def ctx() -> SimpleNamespace:
    return SimpleNamespace(user_data={}, args=[])
