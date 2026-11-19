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

from sheet_data import DATE_FMT, MONTH_FMT, parse_amount, resolve_columns
from sheets import DATA_HEADERS, SheetsClient

# Fields every Data sheet must have a column for. Month is optional: readers
# derive it from the date when the column is absent.
REQUIRED_FIELDS = ("date", "category", "amount", "currency", "who", "comment")


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
