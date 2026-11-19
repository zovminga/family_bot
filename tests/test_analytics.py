#!/usr/bin/env python3
"""Tests for analytics over fixture rows (no Sheets / no HTTP)."""
from __future__ import annotations

from datetime import date

import pytest

from family_bot.analytics import (
    aggregate,
    filter_rows,
    format_last_n,
    format_month_summary,
    last_n,
    month_summary,
    unique_currencies,
)
from family_bot.sheet_data import Row


ROWS = [
    Row(
        date=date(2026, 8, 1), month="2026-08", category="Food",
        amount=10.0, currency="дин", who="Azat", comment="a",
    ),
    Row(
        date=date(2026, 8, 5), month="2026-08", category="Food",
        amount=20.0, currency="€", who="Lisa", comment="b",
    ),
    Row(
        date=date(2026, 7, 10), month="2026-07", category="Transport",
        amount=5.0, currency="дин", who="Azat", comment="c",
    ),
]


def test_unique_currencies():
    assert unique_currencies(ROWS) == ["дин", "€"]
    assert unique_currencies([], fallback=["₽"]) == ["₽"]


def test_last_n_sheet_order_newest_first():
    # last_n follows sheet row order (tail of the list), not calendar sort.
    got = last_n(ROWS, n=2)
    assert [r.comment for r in got] == ["c", "b"]


def test_format_last_n_filtered():
    text = format_last_n(ROWS, n=5, category="Transport")
    assert "Transport" in text
    assert "5 дин" in text


def test_filter_by_month_and_category():
    got = filter_rows(ROWS, category="Food", month="2026-08")
    assert len(got) == 2


def test_filter_by_date_range():
    got = filter_rows(ROWS, date_from="01.08.2026", date_to="05.08.2026")
    assert len(got) == 2
    assert {r.comment for r in got} == {"a", "b"}


def test_aggregate_group_by_currency():
    text, details = aggregate(ROWS, category="All", month="2026-08", group_by_currency=True)
    assert text.splitlines() == ["10 дин", "20 €"]
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
    assert text == "💰 Итого: 2 010 дин"
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
    assert text == "❌ Не удалось получить курс € → дин"
    assert details == {}


def test_format_last_n_shows_date_text():
    text = format_last_n(ROWS, n=1)
    assert "📅 10.07.2026 | Transport" in text


def test_aggregate_convert_needs_rate_source():
    with pytest.raises(ValueError):
        aggregate(ROWS, month="2026-08", convert_to_currency="дин")


# ---------- month summary (issue #35) ----------

def _row(day: int, category: str, amount: float, currency: str, who: str,
         month: str = "2026-11") -> Row:
    return Row(date=date(int(month[:4]), int(month[5:]), day), month=month,
               category=category, amount=amount, currency=currency, who=who, comment="")


MONTH_ROWS = [
    _row(1, "Еда", 1000.0, "дин", "Азат"),
    _row(2, "Еда", 500.0, "дин", "Лиза"),
    _row(3, "Транспорт", 2000.0, "дин", "Азат"),
    _row(4, "Кафе", 300.0, "дин", "Лиза"),
    _row(5, "Дом", 100.0, "дин", "Лиза"),
    _row(6, "Еда", 20.0, "€", "Лиза"),
    _row(30, "Еда", 9999.0, "дин", "Азат", month="2026-10"),
]


def test_month_summary_keeps_currencies_apart():
    summary = month_summary(MONTH_ROWS, "2026-11")
    assert summary.month == "2026-11"
    assert list(summary.by_currency) == ["дин", "€"]  # most rows first
    din = summary.by_currency["дин"]
    assert din.total == 3900.0
    assert din.categories == [("Транспорт", 2000.0), ("Еда", 1500.0),
                              ("Кафе", 300.0), ("Дом", 100.0)]
    assert din.spenders == [("Азат", 3000.0), ("Лиза", 900.0)]
    eur = summary.by_currency["€"]
    assert (eur.total, eur.categories, eur.spenders) == (20.0, [("Еда", 20.0)], [("Лиза", 20.0)])


def test_month_summary_takes_any_month():
    summary = month_summary(MONTH_ROWS, "2026-10")
    assert summary.by_currency["дин"].total == 9999.0
    assert month_summary(MONTH_ROWS, "2025-01").by_currency == {}


def test_format_month_summary_one_currency():
    text = format_month_summary(month_summary(MONTH_ROWS[:5], "2026-11"))
    assert text == (
        "📅 Ноябрь 2026\n"
        "💰 Итого: 3 900 дин\n"
        "\n"
        "🏷 Топ категорий:\n"
        "1. Транспорт — 2 000 дин\n"
        "2. Еда — 1 500 дин\n"
        "3. Кафе — 300 дин\n"
        "\n"
        "👤 По людям:\n"
        "Азат — 3 000 дин\n"
        "Лиза — 900 дин"
    )


def test_format_month_summary_several_currencies_never_added():
    text = format_month_summary(month_summary(MONTH_ROWS, "2026-11"))
    assert text.startswith("📅 Ноябрь 2026\n💰 Итого: 3 900 дин · 20 €\n")
    assert "\n— дин —\n" in text and "\n— € —\n" in text
    assert "3 920" not in text
    assert text.endswith("👤 По людям:\nЛиза — 20 €")


def test_format_month_summary_empty_month():
    assert format_month_summary(month_summary([], "2026-11")) == "📅 Ноябрь 2026\nНет данных 🤷"


def test_month_summary_blank_category_and_spender_get_labels():
    rows = [_row(1, "", 10.0, "дин", "")]
    din = month_summary(rows, "2026-11").by_currency["дин"]
    assert din.categories == [("Без категории", 10.0)]
    assert din.spenders == [("Не указан", 10.0)]
