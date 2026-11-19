#!/usr/bin/env python3
"""Tests for the SheetsClient seam (fake adapter, no network)."""
from __future__ import annotations

import pytest

from family_bot.regular import parse_regular
from family_bot.sheets import GspreadSheetsClient, resolve_creds_path
# Kept importable from here for branches written before the fake moved to conftest.
from tests.conftest import FakeSheetsClient


def test_fake_append_and_read_roundtrip():
    client = FakeSheetsClient(data_values=[
        ["Date", "Month", "Category", "Amount", "Currency", "Who", "Comment"],
    ])
    row = ["07.08.2026", "2026-08", "Food", "12.5", "дин", "Ann", "lunch"]
    client.append_data_row(row)

    assert client.appended_rows == [row]
    assert client.read_data_records()[0]["Category"] == "Food"
    assert client.read_data_headers()[0] == "Date"


def test_fake_dashboard_file_id():
    client = FakeSheetsClient()
    assert client.get_dashboard_file_id() is None
    client.set_dashboard_file_id("AgADBAAD...")
    assert client.get_dashboard_file_id() == "AgADBAAD..."


def test_regular_load_presets_uses_client(monkeypatch):
    from family_bot import regular

    fake = FakeSheetsClient(regular_values=[
        ["name", "динары", "евро", "доллары", "рубли", "категория", "кто"],
        ["ежемесячные траты", "", "", "", "", "", ""],
        ["Rent", "", "850", "", "", "Rent", "Both"],
        ["разовые траты", "", "", "", "", "", ""],
        ["баня", "3850", "", "", "", "Beauty", "Both"],
    ])

    items = regular.load_presets(fake)
    assert len(items) == 2
    assert items[0]["name"] == "Rent"
    assert items[0]["section"] == "monthly"
    assert items[0]["amount"] == 850.0
    assert items[0]["currency"] == "€"
    assert items[1]["name"] == "баня"
    assert items[1]["section"] == "oneoff"


def test_parse_regular_skips_blank_amounts():
    values = [
        ["name", "динары", "категория", "кто"],
        ["Subscription", "", "Other", "Both"],
        ["Coffee", "200", "Food", "Ann"],
    ]
    parsed = parse_regular(values)
    assert parsed["monthly"] == [
        {"name": "Coffee", "amount": 200.0, "currency": "дин",
         "category": "Food", "who": "Ann"},
    ]
    assert parsed["oneoff"] == []


def test_resolve_creds_path_relative(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GOOGLE_CREDS_PATH", "creds.json")
    # Relative path resolves against sheets.BOT_DIR, not cwd.
    from family_bot.sheets import BOT_DIR

    path = resolve_creds_path()
    assert path == (BOT_DIR / "creds.json").resolve()


def test_resolve_creds_path_missing_env(monkeypatch):
    monkeypatch.delenv("GOOGLE_CREDS_PATH", raising=False)
    with pytest.raises(RuntimeError, match="GOOGLE_CREDS_PATH"):
        resolve_creds_path()


def test_gspread_append_writes_values_raw():
    calls = []

    class FakeWorksheet:
        def append_row(self, row, **kw):
            calls.append((row, kw))

    class FakeSpreadsheet:
        def worksheet(self, name):
            assert name == "Data"
            return FakeWorksheet()

    row = ["07.08.2026", "2026-08", "Food", 1.0, "€", "Ann", "=1+1"]
    GspreadSheetsClient(FakeSpreadsheet()).append_data_row(row)
    assert calls == [(row, {"value_input_option": "RAW"})]


def test_gspread_append_data_rows_is_one_raw_request():
    calls = []

    class FakeWorksheet:
        def append_rows(self, rows, **kw):
            calls.append((rows, kw))

    class FakeSpreadsheet:
        def worksheet(self, name):
            assert name == "Data"
            return FakeWorksheet()

    rows = [
        ["07.08.2026", "2026-08", "Food", 1.0, "€", "Ann", "a"],
        ["07.08.2026", "2026-08", "Food", 2.0, "€", "Ann", "b"],
    ]
    GspreadSheetsClient(FakeSpreadsheet()).append_data_rows(rows)
    assert calls == [(rows, {"value_input_option": "RAW"})]


def test_missing_spreadsheet_raises_domain_error(monkeypatch, tmp_path):
    import gspread

    from family_bot import sheets

    creds = tmp_path / "creds.json"
    creds.write_text("{}")

    class _Client:
        def open(self, name):
            raise gspread.SpreadsheetNotFound("404")

    monkeypatch.setattr(gspread, "service_account", lambda **kw: _Client())
    try:
        sheets._authorize_spreadsheet(creds_path=str(creds), sheet_name="Family")
    except sheets.SpreadsheetNotFound as e:
        assert "Family" in str(e)
    else:
        raise AssertionError("no SpreadsheetNotFound")
