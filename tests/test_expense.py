#!/usr/bin/env python3
"""Tests for the ExpenseRecord write model."""
from __future__ import annotations

import pytest

from expense import ExpenseRecord, append_expense
from tests.conftest import FakeSheetsClient


def test_to_row_includes_derived_month():
    record = ExpenseRecord(
        date="07.08.2026",
        category="Food",
        amount=12.5,
        currency="дин",
        who="Azat",
        comment="lunch",
    )
    assert record.month() == "2026-08"
    assert record.to_row() == [
        "07.08.2026", "2026-08", "Food", 12.5, "дин", "Azat", "lunch",
    ]


def test_to_row_default_comment_empty():
    record = ExpenseRecord(
        date="01.01.2025",
        category="Rent",
        amount=850,
        currency="€",
        who="Both",
    )
    assert record.to_row()[6] == ""


def test_append_expense_uses_client_seam():
    client = FakeSheetsClient()
    record = ExpenseRecord(
        date="07.08.2026",
        category="Food",
        amount="7,65",
        currency="₽",
        who="Lisa",
        comment="tea",
    )
    append_expense(client, record)
    assert client.appended_rows == [record.to_row()]
    assert client.read_data_records()[0]["Category"] == "Food"


def test_month_rejects_bad_date():
    record = ExpenseRecord(
        date="not-a-date",
        category="Other",
        amount=1,
        currency="€",
        who="Azat",
    )
    with pytest.raises(ValueError):
        record.month()
