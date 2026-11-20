"""Pytest configuration: keep repo root on sys.path, isolate from secrets.

Importing bot.py reads no .env and opens no spreadsheet (Config loads in
post_init). ``bot.main()`` does read .env, so a checkout that holds a real one
(for example the copy the dashboard launchd job uses) could reach live
services from a test. Neutralise .env and the secret variables up front.

Also holds FakeSheetsClient, the in-memory SheetsClient shared by unit tests
(`from tests.conftest import FakeSheetsClient`), and ``bot_context``.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator, Optional

import dotenv
import pytest

from family_bot import logs

dotenv.load_dotenv = lambda *args, **kwargs: False
for _var in ("GOOGLE_CREDS_PATH", "SHEET_NAME", "BOT_TOKEN", "ANTHROPIC_API_KEY",
             "DASHBOARD_CHAT_ID"):
    os.environ.pop(_var, None)

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# Currencies the stub rate service lists in tests (no network).
TEST_CURRENCY_CODES = ["AED", "CAD", "CNY", "EUR", "GBP", "GEL", "IQD", "JPY", "RSD",
                       "RUB", "TRY", "USD"]


@pytest.fixture(autouse=True)
def _restore_logging() -> Iterator[None]:
    """Undo logs.setup() from entry points called in a test (root handlers, levels)."""
    root = logging.getLogger()
    quiet = {name: logging.getLogger(name).level for name in logs.QUIET_LOGGERS}
    saved = (list(root.handlers), root.level)
    yield
    root.handlers[:] = saved[0]
    root.setLevel(saved[1])
    for name, level in quiet.items():
        logging.getLogger(name).setLevel(level)


@pytest.fixture(autouse=True)
def _offline_currency_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    """The «🌍 Другая» list reads TEST_CURRENCY_CODES instead of the rate service."""
    from family_bot import currency_picker

    monkeypatch.setattr(currency_picker, "CATALOG",
                        currency_picker.Catalog(fetch=lambda: list(TEST_CURRENCY_CODES)))


@pytest.fixture(autouse=True)
def _offline_rates(monkeypatch: pytest.MonkeyPatch) -> None:
    """``fx.rate`` never reaches the rate service; a test that needs rates sets fx.RATES."""
    import requests

    from family_bot import fx

    def no_network(base: str) -> dict[str, float]:
        raise requests.ConnectionError(f"no network in tests ({base})")

    monkeypatch.setattr(fx, "RATES", fx.RateCache(fetch=no_network))


def bot_context(**fields: Any) -> SimpleNamespace:
    """Handler context holding a Config in bot_data, as after post_init.

    Defaults: Allowlist 111:Ann, 222:Bob, fallback categories, no Sheets.
    Pass any Config field to override (``sheets=FakeSheets()``).
    """
    from family_bot import config

    settings = config.Config(**{
        "allowed_users": {111: "Ann", 222: "Bob"},
        "categories": list(config.FALLBACK_CATEGORIES),
        **fields,
    })
    return SimpleNamespace(user_data={}, args=[], bot_data={config.KEY: settings})


def settings_of(context: SimpleNamespace) -> Any:
    """The Config inside a ``bot_context``."""
    from family_bot import config

    return context.bot_data[config.KEY]


class FakeSheetsClient:
    """In-memory adapter satisfying SheetsClient for unit tests."""

    def __init__(
        self,
        *,
        categories: list[str] | None = None,
        data_values: list[list[str]] | None = None,
        regular_values: list[list[str]] | None = None,
        dashboard_file_id: str | None = None,
        url: str | None = "https://example.test/sheet",
        connection_ok: bool = True,
    ):
        self._categories = list(categories or [])
        self._data_values = [list(r) for r in (data_values or [])]
        self._regular_values = [list(r) for r in (regular_values or [])]
        self._dashboard_file_id = dashboard_file_id
        self._url = url
        self._connection_ok = connection_ok
        self.appended_rows: list[list] = []
        # Users sheet: Recent currencies and Spender name by Telegram id.
        self.users: dict[int, list[str]] = {}
        self.user_names: dict[int, str] = {}

    @property
    def url(self) -> Optional[str]:
        return self._url

    def test_connection(self) -> bool:
        return self._connection_ok

    def read_categories(self) -> list[str]:
        return list(self._categories)

    def read_data_values(self) -> list[list[str]]:
        return [list(r) for r in self._data_values]

    def read_data_records(self) -> list[dict]:
        if not self._data_values:
            return []
        headers = self._data_values[0]
        records = []
        for row in self._data_values[1:]:
            records.append({
                headers[i]: (row[i] if i < len(row) else "")
                for i in range(len(headers))
            })
        return records

    def read_data_headers(self) -> list[str]:
        return list(self._data_values[0]) if self._data_values else []

    def add_data_column(self, header: str) -> list[str]:
        self._data_values[0].append(header)
        return list(self._data_values[0])

    def append_data_row(self, row: list) -> None:
        self.appended_rows.append(list(row))
        if not self._data_values:
            self._data_values = [["Date", "Month", "Category", "Amount",
                                  "Currency", "Who", "Comment"]]
        self._data_values.append([str(c) for c in row])

    def append_data_rows(self, rows: list[list]) -> None:
        for row in rows:
            self.append_data_row(row)

    def delete_data_rows(self, row_numbers: list[int]) -> None:
        for n in sorted(set(row_numbers), reverse=True):
            del self._data_values[n - 1]

    def read_regular_values(self) -> list[list[str]]:
        return [list(r) for r in self._regular_values]

    def get_dashboard_file_id(self) -> Optional[str]:
        return self._dashboard_file_id

    def set_dashboard_file_id(self, file_id: str) -> None:
        self._dashboard_file_id = file_id

    def read_recent_currencies(self, user_id: int) -> Optional[list[str]]:
        found = self.users.get(user_id)
        return list(found) if found else None

    def write_recent_currencies(self, user_id: int, name: str, values: list[str]) -> None:
        self.users[user_id] = list(values)
        self.user_names[user_id] = name
