#!/usr/bin/env python3
"""Expense write model: one record shape, one append path.

Callers build an ExpenseRecord; append_expense owns column order and month
derivation so the Data sheet schema has a single locality.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Union

from sheet_data import DATE_FMT, MONTH_FMT
from sheets import SheetsClient


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

    def to_row(self) -> list:
        """Data sheet column order: Date, Month, Category, Amount, Currency, Who, Comment."""
        return [
            self.date,
            self.month(),
            self.category,
            self.amount,
            self.currency,
            self.who,
            self.comment,
        ]


def append_expense(client: SheetsClient, record: ExpenseRecord) -> None:
    """Persist one Expense through the SheetsClient seam."""
    client.append_data_row(record.to_row())
