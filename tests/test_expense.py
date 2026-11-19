#!/usr/bin/env python3
"""Tests for the ExpenseRecord write model."""
from __future__ import annotations

import pytest

from expense import ExpenseRecord, append_expense
from tests.test_sheets import FakeSheetsClient


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
    assert client.appended_rows[0][3] == 7.65
    assert client.read_data_records()[0]["Category"] == "Food"


def test_append_expense_lays_row_out_by_headers():
    client = FakeSheetsClient(data_values=[
        ["Who", "Дата", "Сумма", "Category", "Notes", "Валюта", "Comment"],
    ])
    append_expense(client, ExpenseRecord(
        date="07.08.2026", category="Food", amount=12.5, currency="дин",
        who="Azat", comment="lunch",
    ))
    # No Month column: month is not written; unknown "Notes" stays empty.
    assert client.appended_rows == [
        ["Azat", "07.08.2026", 12.5, "Food", "", "дин", "lunch"],
    ]


def test_append_expense_without_headers_uses_default_layout():
    client = FakeSheetsClient()
    record = ExpenseRecord(date="07.08.2026", category="Food", amount=1,
                           currency="€", who="Lisa")
    append_expense(client, record)
    assert client.appended_rows == [record.to_row()]


def test_to_row_rejects_headers_missing_a_field():
    record = ExpenseRecord(date="07.08.2026", category="Food", amount=1,
                           currency="€", who="Lisa")
    with pytest.raises(ValueError, match="currency"):
        record.to_row(["Date", "Category", "Amount", "Who", "Comment"])


def test_to_row_writes_text_amount_as_number():
    record = ExpenseRecord(date="07.08.2026", category="Food", amount="7,65",
                           currency="€", who="Lisa")
    assert record.to_row()[3] == 7.65


def test_to_row_rejects_unparseable_amount():
    record = ExpenseRecord(date="07.08.2026", category="Food", amount="abc",
                           currency="€", who="Lisa")
    with pytest.raises(ValueError, match="amount"):
        record.to_row()


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
