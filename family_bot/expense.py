#!/usr/bin/env python3
"""Expense write model: one record shape, one append path.

Callers build an ExpenseRecord; append_expense lays the row out by the Data
sheet's header row and derives the month, so the Data sheet schema has a
single locality.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Union

from family_bot.sheet_data import DATE_FMT, MONTH_FMT, parse_amount, resolve_columns
from family_bot.sheets import DATA_HEADERS, SheetsClient

# Fields every Data sheet must have a column for. Month is optional: readers
# derive it from the date when the column is absent.
REQUIRED_FIELDS = ("date", "category", "amount", "currency", "who", "comment")
# Text fields compared when looking a written Expense up again (amount apart).
MATCH_TEXT_FIELDS = ("date", "category", "currency", "who", "comment")
# Amounts are stored to cents, so two amounts closer than this are equal.
AMOUNT_TOLERANCE = 0.005


@dataclass(frozen=True)
class ExpenseRecord:
    """One Expense ready to append to the Data sheet."""

    date: str  # DD.MM.YYYY
    category: str
    amount: Union[int, float, str]
    currency: str
    who: str
    comment: str = ""

    def month(self) -> str:
        return datetime.strptime(self.date, DATE_FMT).strftime(MONTH_FMT)

    def to_row(self, headers: Optional[list[str]] = None) -> list:
        """Lay the record out by Data header names (aliases from sheet_data).

        Without headers (empty sheet) the default DATA_HEADERS order is used.
        Columns the record does not know stay empty. A text amount is written
        as a number, since writes are RAW.
        """
        if not headers or not any(str(h).strip() for h in headers):
            headers = DATA_HEADERS
        columns = resolve_columns(headers)
        missing = [f for f in REQUIRED_FIELDS if f not in columns]
        if missing:
            raise ValueError(f"Data sheet has no column for: {', '.join(missing)}")

        amount = self.amount
        if isinstance(amount, str):
            parsed = parse_amount(amount)
            if parsed is None:
                raise ValueError(f"Unparseable amount: {self.amount!r}")
            amount = parsed

        values = {
            "date": self.date,
            "month": self.month(),
            "category": self.category,
            "amount": amount,
            "currency": self.currency,
            "who": self.who,
            "comment": self.comment,
        }
        row: list = [""] * (max(columns.values()) + 1)
        for field, idx in columns.items():
            row[idx] = values[field]
        return row


def append_expense(client: SheetsClient, record: ExpenseRecord) -> None:
    """Persist one Expense through the SheetsClient seam, matching Data headers."""
    client.append_data_row(record.to_row(client.read_data_headers()))


def append_expenses(client: SheetsClient, records: list[ExpenseRecord]) -> None:
    """Persist several Expenses in one append call: all rows land or none do."""
    headers = client.read_data_headers()
    client.append_data_rows([r.to_row(headers) for r in records])


def _matches(row: list, columns: dict[str, int], record: ExpenseRecord) -> bool:
    """True if a Data row (as read back, formatted) holds exactly this record."""
    def cell(field: str) -> str:
        idx = columns[field]
        return str(row[idx]).strip() if idx < len(row) else ""

    if any(cell(f) != str(getattr(record, f)).strip() for f in MATCH_TEXT_FIELDS):
        return False
    stored = parse_amount(cell("amount"))
    wanted = parse_amount(record.amount)
    return stored is not None and wanted is not None and abs(stored - wanted) < AMOUNT_TOLERANCE


def delete_expenses(client: SheetsClient, records: list[ExpenseRecord]) -> bool:
    """Delete the Data rows holding these just-written records, all or none.

    Data has no row id, so a row is found by its values: every field must equal
    what was written (the amount compared as a number, since the sheet may
    format it). The bottom-most match is taken, each row at most once.
    Identical rows are interchangeable, so taking any of them leaves the same
    data. A record with no matching row (edited or deleted in the sheet since)
    deletes nothing and returns False.
    """
    values = client.read_data_values()
    if not values:
        return False
    columns = resolve_columns(values[0])
    if any(f not in columns for f in REQUIRED_FIELDS):
        return False
    taken: set[int] = set()
    for record in records:
        found = next(
            (n for n in range(len(values), 1, -1)
             if n not in taken and _matches(values[n - 1], columns, record)),
            None,
        )
        if found is None:
            return False
        taken.add(found)
    client.delete_data_rows(sorted(taken))
    return True
