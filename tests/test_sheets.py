#!/usr/bin/env python3
"""Tests for the SheetsClient seam (fake adapter, no network)."""
from __future__ import annotations

import pytest

from regular import parse_regular
from sheets import resolve_creds_path
# Kept importable from here for branches written before the fake moved to conftest.
from tests.conftest import FakeSheetsClient


def test_fake_append_and_read_roundtrip():
    client = FakeSheetsClient(data_values=[
        ["Date", "Month", "Category", "Amount", "Currency", "Who", "Comment"],
    ])
    row = ["07.08.2026", "2026-08", "Food", "12.5", "дин", "Azat", "lunch"]
    client.append_data_row(row)

    assert client.appended_rows == [row]
    assert client.read_data_records()[0]["Category"] == "Food"
    assert client.read_data_headers()[0] == "Date"


def test_fake_dashboard_file_id():
    client = FakeSheetsClient()
    assert client.get_dashboard_file_id() is None
    client.set_dashboard_file_id("AgADBAAD...")
    assert client.get_dashboard_file_id() == "AgADBAAD..."


def test_regular_load_defaults_uses_client(monkeypatch):
    import regular

    fake = FakeSheetsClient(regular_values=[
        ["name", "динары", "евро", "доллары", "рубли", "категория", "кто"],
        ["ежемесячные траты", "", "", "", "", "", ""],
        ["Rent", "", "850", "", "", "Rent", "Both"],
        ["разовые траты", "", "", "", "", "", ""],
        ["баня", "3850", "", "", "", "Beauty", "Both"],
    ])

    items = regular.load_defaults(fake)
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
        ["Coffee", "200", "Food", "Azat"],
    ]
    parsed = parse_regular(values)
    assert parsed["monthly"] == [
        {"name": "Coffee", "amount": 200.0, "currency": "дин",
         "category": "Food", "who": "Azat"},
    ]
    assert parsed["oneoff"] == []


def test_resolve_creds_path_relative(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GOOGLE_CREDS_PATH", "creds.json")
    # Relative path resolves against sheets.BOT_DIR, not cwd.
    from sheets import BOT_DIR

    path = resolve_creds_path()
    assert path == (BOT_DIR / "creds.json").resolve()


def test_resolve_creds_path_missing_env(monkeypatch):
    monkeypatch.delenv("GOOGLE_CREDS_PATH", raising=False)
    with pytest.raises(RuntimeError, match="GOOGLE_CREDS_PATH"):
        resolve_creds_path()
