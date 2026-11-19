#!/usr/bin/env python3
"""Tests for analytics over fixture rows (no Sheets / no HTTP)."""
from __future__ import annotations

from family_bot.analytics import (
    aggregate,
    filter_rows,
    format_last_n,
    last_n,
    unique_currencies,
)


ROWS = [
    {
        "date": "01.08.2026", "month": "2026-08", "category": "Food",
        "amount": 10.0, "currency": "дин", "who": "Azat", "comment": "a",
    },
    {
        "date": "05.08.2026", "month": "2026-08", "category": "Food",
        "amount": 20.0, "currency": "€", "who": "Lisa", "comment": "b",
    },
    {
        "date": "10.07.2026", "month": "2026-07", "category": "Transport",
        "amount": 5.0, "currency": "дин", "who": "Azat", "comment": "c",
    },
]


def test_unique_currencies():
    assert unique_currencies(ROWS) == ["дин", "€"]
    assert unique_currencies([], fallback=["₽"]) == ["₽"]


def test_last_n_sheet_order_newest_first():
    # last_n follows sheet row order (tail of the list), not calendar sort.
    got = last_n(ROWS, n=2)
    assert [r["comment"] for r in got] == ["c", "b"]


def test_format_last_n_filtered():
    text = format_last_n(ROWS, n=5, category="Transport")
    assert "Transport" in text
    assert "5.00" in text or "5,00" in text


def test_filter_by_month_and_category():
    got = filter_rows(ROWS, category="Food", month="2026-08")
    assert len(got) == 2


def test_filter_by_date_range():
    got = filter_rows(ROWS, date_from="01.08.2026", date_to="05.08.2026")
    assert len(got) == 2
    assert {r["comment"] for r in got} == {"a", "b"}


def test_aggregate_group_by_currency():
    text, details = aggregate(ROWS, category="All", month="2026-08", group_by_currency=True)
    assert "дин: 10.00" in text or "дин: 10,00" in text
    assert "€: 20.00" in text or "€: 20,00" in text
    assert details == {}


def test_aggregate_convert_with_fake_rate():
    def fake_rate(frm, to):
        assert to == "дин"
        if frm == "€":
            return 100.0
        return 1.0

    text, details = aggregate(
        ROWS,
        category="All",
        month="2026-08",
        group_by_currency=False,
        convert_to_currency="дин",
        get_rate=fake_rate,
    )
    # 10 дин + 20€*100 = 2010
    assert "2,010.00" in text or "2010.00" in text
    assert details["€"]["rate"] == 100.0
    assert details["€"]["original_amount"] == 20.0
    assert details["€"]["converted_amount"] == 2000.0


def test_aggregate_missing_rate():
    text, details = aggregate(
        ROWS,
        month="2026-08",
        convert_to_currency="дин",
        get_rate=lambda *_: None,
    )
    assert text.startswith("❌ Failed to get exchange rate")
    assert details == {}
