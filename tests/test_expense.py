#!/usr/bin/env python3
"""Tests for the ExpenseRecord write model."""
from __future__ import annotations

import pytest

from family_bot.expense import ExpenseRecord, append_expense, append_expenses, delete_expenses
from tests.conftest import FakeSheetsClient


def test_to_row_includes_derived_month():
    record = ExpenseRecord(
        date="07.08.2026",
        category="Food",
        amount=12.5,
        currency="дин",
        who="Ann",
        comment="lunch",
    )
    assert record.month() == "2026-08"
    assert record.to_row() == [
        "07.08.2026", "2026-08", "Food", 12.5, "дин", "Ann", "lunch", "",
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
        who="Bob",
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
        who="Ann", comment="lunch",
    ))
    # No Month column: month is not written; unknown "Notes" stays empty.
    assert client.appended_rows == [
        ["Ann", "07.08.2026", 12.5, "Food", "", "дин", "lunch"],
    ]


def test_append_expense_without_headers_uses_default_layout():
    client = FakeSheetsClient()
    record = ExpenseRecord(date="07.08.2026", category="Food", amount=1,
                           currency="€", who="Bob")
    append_expense(client, record)
    assert client.appended_rows == [record.to_row()]


def test_to_row_rejects_headers_missing_a_field():
    record = ExpenseRecord(date="07.08.2026", category="Food", amount=1,
                           currency="€", who="Bob")
    with pytest.raises(ValueError, match="currency"):
        record.to_row(["Date", "Category", "Amount", "Who", "Comment"])


def test_to_row_writes_text_amount_as_number():
    record = ExpenseRecord(date="07.08.2026", category="Food", amount="7,65",
                           currency="€", who="Bob")
    assert record.to_row()[3] == 7.65


def test_to_row_rejects_unparseable_amount():
    record = ExpenseRecord(date="07.08.2026", category="Food", amount="abc",
                           currency="€", who="Bob")
    with pytest.raises(ValueError, match="amount"):
        record.to_row()


def test_month_rejects_bad_date():
    record = ExpenseRecord(
        date="not-a-date",
        category="Other",
        amount=1,
        currency="€",
        who="Ann",
    )
    with pytest.raises(ValueError):
        record.month()


def test_append_expenses_reads_headers_once_and_appends_one_batch():
    calls: list[list[list]] = []

    class BatchClient(FakeSheetsClient):
        def append_data_rows(self, rows):
            calls.append(rows)

    client = BatchClient(data_values=[
        ["Who", "Date", "Amount", "Category", "Currency", "Comment"],
    ])
    append_expenses(client, [
        ExpenseRecord(date="07.08.2026", category="Food", amount=1,
                      currency="€", who="Bob", comment="a"),
        ExpenseRecord(date="07.08.2026", category="Food", amount=2,
                      currency="€", who="Ann", comment="b"),
    ])
    assert calls == [[
        ["Bob", "07.08.2026", 1, "Food", "€", "a"],
        ["Ann", "07.08.2026", 2, "Food", "€", "b"],
    ]]


# --- delete_expenses (undo) ---

HEADERS = ["Date", "Month", "Category", "Amount", "Currency", "Who", "Comment"]


def _rec(amount=350, comment="", who="Azat"):
    return ExpenseRecord(date="19.11.2026", category="Food", amount=amount,
                         currency="дин", who=who, comment=comment)


def test_delete_expenses_removes_the_matching_row_not_the_last_one():
    client = FakeSheetsClient(data_values=[
        HEADERS,
        ["19.11.2026", "2026-11", "Food", "350", "дин", "Azat", "coffee"],
        ["19.11.2026", "2026-11", "Food", "90", "дин", "Lisa", "bread"],  # added later
    ])
    assert delete_expenses(client, [_rec(comment="coffee")]) is True
    assert [r[6] for r in client.read_data_values()[1:]] == ["bread"]


def test_delete_expenses_reads_formatted_amounts():
    client = FakeSheetsClient(data_values=[
        HEADERS, ["19.11.2026", "2026-11", "Food", "1 234,50", "дин", "Azat", ""],
    ])
    assert delete_expenses(client, [_rec(amount=1234.5)]) is True
    assert client.read_data_values() == [HEADERS]


def test_delete_expenses_refuses_when_row_was_changed():
    client = FakeSheetsClient(data_values=[
        HEADERS, ["19.11.2026", "2026-11", "Food", "360", "дин", "Azat", ""],
    ])
    assert delete_expenses(client, [_rec()]) is False
    assert len(client.read_data_values()) == 2


def test_delete_expenses_all_or_nothing_for_several_rows():
    client = FakeSheetsClient(data_values=[
        HEADERS, ["19.11.2026", "2026-11", "Food", "350", "дин", "Azat", "a"],
    ])
    assert delete_expenses(client, [_rec(comment="a"), _rec(comment="b")]) is False
    assert len(client.read_data_values()) == 2


def test_delete_expenses_takes_the_latest_of_identical_rows_once_each():
    row = ["19.11.2026", "2026-11", "Food", "350", "дин", "Azat", ""]
    client = FakeSheetsClient(data_values=[HEADERS, row, ["x"] * 7, row, row])
    assert delete_expenses(client, [_rec(), _rec()]) is True
    assert client.read_data_values() == [HEADERS, row, ["x"] * 7]


def test_delete_expenses_matches_by_header_names():
    headers = ["Who", "Comment", "Amount", "Currency", "Category", "Date"]
    client = FakeSheetsClient(data_values=[
        headers, ["Azat", "", "350", "дин", "Food", "19.11.2026"],
    ])
    assert delete_expenses(client, [_rec()]) is True
