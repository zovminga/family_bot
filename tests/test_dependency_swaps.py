"""Code paths that changed when oauth2client and pandas were dropped (issue #9)."""
from __future__ import annotations

import asyncio
import os
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import dotenv
import gspread
import pytest

# bot.py reads .env and reaches Sheets at import time: neutralise both first,
# the same way tests/known_bugs/conftest.py does.
dotenv.load_dotenv = lambda *args, **kwargs: False
for _var in ("GOOGLE_CREDS_PATH", "SHEET_NAME", "BOT_TOKEN"):
    os.environ.pop(_var, None)

import bot  # noqa: E402
import sheets  # noqa: E402
from tests.known_bugs.fakes import make_update  # noqa: E402


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz: Any = None) -> "_FrozenDatetime":
        return cls(2026, 3, 31, 12, 0)


def test_by_months_lists_last_twelve_months_newest_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bot, "datetime", _FrozenDatetime)
    update = make_update("📆 By months")

    asyncio.new_event_loop().run_until_complete(
        bot.stat_type(update, SimpleNamespace(user_data={}, args=[]))  # type: ignore[arg-type]
    )

    _, kwargs = update.message.replies[-1]
    labels = [row[0].text for row in kwargs["reply_markup"].keyboard]
    assert labels[:12] == [
        "2026-03", "2026-02", "2026-01", "2025-12", "2025-11", "2025-10",
        "2025-09", "2025-08", "2025-07", "2025-06", "2025-05", "2025-04",
    ]


def test_authorize_opens_sheet_with_service_account(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    creds = tmp_path / "creds.json"
    creds.write_text("{}")
    calls: dict[str, Any] = {}

    class _Client:
        def open(self, name: str) -> str:
            calls["open"] = name
            return "spreadsheet"

    def fake_service_account(**kwargs: Any) -> _Client:
        calls["auth"] = kwargs
        return _Client()

    monkeypatch.setattr(gspread, "service_account", fake_service_account)

    result = sheets._authorize_spreadsheet(creds_path=str(creds), sheet_name="Family")

    assert result == "spreadsheet"
    assert calls["auth"] == {"filename": str(creds), "scopes": sheets.SCOPE}
    assert calls["open"] == "Family"
