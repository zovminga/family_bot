#!/usr/bin/env python3
"""Tests for the shared sheet_data parsing kernel."""
from __future__ import annotations

import pytest

from family_bot.sheet_data import (
    normalize_month,
    normalize_rows,
    parse_amount,
    resolve_columns,
    to_iso,
)


def test_parse_amount_comma_decimal():
    assert parse_amount("7,65") == 7.65
    assert parse_amount("12.5") == 12.5


def test_parse_amount_both_separators_ru():
    assert parse_amount("1.234,56") == 1234.56


def test_parse_amount_thousands_comma():
    # Four digits after comma → thousands, not decimal.
    assert parse_amount("1,2345") == 12345.0


@pytest.mark.parametrize("raw,expected", [
    ("12,5", 12.5),          # one comma, 1-2 digits after it: decimal comma
    ("7,65", 7.65),
    ("1,234", 1234.0),       # one comma, exactly 3 digits after it: thousands
    ("12,500", 12500.0),
    ("1,234,567", 1234567.0),
    ("12.5", 12.5),          # dot alone is a decimal point
    ("1.234,56", 1234.56),   # both: the rightmost separator is the decimal one
    ("1,234.56", 1234.56),
    ("1 234,5", 1234.5),
])
def test_parse_amount_separator_rule(raw, expected):
    assert parse_amount(raw) == expected


def test_parse_amount_blank():
    assert parse_amount("") is None
    assert parse_amount(None) is None


def test_resolve_columns_ru_en():
    cols = resolve_columns(["Дата", "Month", "Category", "Сумма", "Валюта", "Who", "Comment"])
    assert cols["date"] == 0
    assert cols["amount"] == 3
    assert cols["who"] == 5


def test_normalize_month_from_date():
    assert normalize_month("07.08.2026", "") == "2026-08"
    assert normalize_month("07.08.2026", "2026-07") == "2026-07"


def test_normalize_rows_skips_bad_amounts():
    headers = ["Date", "Month", "Category", "Amount", "Currency", "Who", "Comment"]
    rows = [
        ["07.08.2026", "2026-08", "Food", "7,65", "дин", "Azat", "lunch"],
        ["08.08.2026", "", "Transport", "", "€", "Lisa", "bus"],
        ["09.08.2026", "", "Rent", "850", "€", "Both", "rent"],
    ]
    out = normalize_rows(headers, rows)
    assert len(out) == 2
    assert out[0]["amount"] == 7.65
    assert out[0]["currency"] == "дин"
    assert out[1]["month"] == "2026-08"
    assert out[1]["category"] == "Rent"


def test_to_iso():
    assert to_iso("дин") == "RSD"
    assert to_iso("EUR") == "EUR"
