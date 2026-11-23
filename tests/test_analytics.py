#!/usr/bin/env python3
"""Tests for analytics over fixture rows (no Sheets / no HTTP)."""
from __future__ import annotations

from datetime import date

import pytest

from family_bot.analytics import (
    convert_rows,
    filter_rows,
    format_last_n,
    format_month_summary,
    format_period_stats,
    last_n,
    month_summary,
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


H = "📊 <b>Траты</b>"


def test_period_stats_month_look_block_per_currency():
    text = format_period_stats(ROWS, H, month="2026-08")
    assert text.startswith(f"{H}\n\n💰 Итого <b>10 дин</b>\n\n<b>Food</b> 10\n██████████ 100%")
    assert "💰 Итого <b>20 €</b>" in text
    assert "<blockquote expandable>👤 По людям\nAzat — 10 дин\nLisa — 20 €</blockquote>" in text


def test_period_stats_convert_puts_rate_in_collapsed_block():
    def fake_rate(frm, to):
        assert to == "дин"
        return 100.0 if frm == "€" else 1.0

    text = format_period_stats(ROWS, H, month="2026-08", convert_to="дин", get_rate=fake_rate)
    # 10 дин + 20€*100 = 2010
    assert "💰 Итого <b>2 010 дин</b>" in text and "Итого <b>20 €" not in text
    collapsed = text[text.index("<blockquote expandable>"):]
    assert "💱 Курс\n20 € → 2 000 дин (курс 100)" in collapsed


def test_period_stats_missing_rate():
    text = format_period_stats(ROWS, H, month="2026-08", convert_to="дин",
                               get_rate=lambda *_: None)
    assert text == f"{H}\n\n❌ Нет курса для пересчёта в дин: €."


def test_convert_rows_names_every_missing_currency():
    rows = [*ROWS, Row(date=date(2026, 8, 3), month="2026-08", category="Food", amount=5.0,
                       currency="GEL", who="Ann", comment="")]
    _, rates, missing = convert_rows(rows, "дин", lambda src, dst: 100.0 if src == "€" else None)
    assert missing == ["GEL"] and rates == {}


def test_format_last_n_lines_like_saved_line():
    text = format_last_n(ROWS, n=2)
    assert text == ("📜 <b>Последние записи</b>\n"
                    "<b>c — 5 дин</b> · Transport · 10.07 · Azat\n"
                    "<b>b — 20 €</b> · Food · 05.08 · Lisa")


def test_format_last_n_without_comment_category_leads():
    row = Row(date=date(2026, 11, 23), month="2026-11", category="Еда", amount=350.0,
              currency="дин", who="Аня", comment="")
    assert format_last_n([row], n=1).endswith("\n<b>Еда — 350 дин</b> · 23.11 · Аня")


def test_period_stats_convert_needs_rate_source():
    with pytest.raises(ValueError):
        format_period_stats(ROWS, H, month="2026-08", convert_to="дин")


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
        "📅 <b>Итог за ноябрь 2026</b>\n"
        "\n"
        "💰 Итого <b>3 900 дин</b>\n"
        "\n"
        "<b>Транспорт</b> 2 000\n"
        "█████░░░░░ 51%\n"
        "<b>Еда</b> 1 500\n"
        "████░░░░░░ 38%\n"
        "<b>Кафе</b> 300\n"
        "█░░░░░░░░░ 8%\n"
        "<b>Дом</b> 100\n"
        "░░░░░░░░░░ 3%\n"
        "\n"
        "<blockquote expandable>👤 По людям\n"
        "Азат — 3 000 дин\n"
        "Лиза — 900 дин</blockquote>"
    )


def test_format_month_summary_two_currencies_own_blocks_never_added():
    text = format_month_summary(month_summary(MONTH_ROWS, "2026-11"))
    blocks = text.split("\n\n")
    assert blocks[1] == "💰 Итого <b>3 900 дин</b>"
    # Each Currency has its own block; bars are shares of that Currency's total.
    assert blocks[3] == "💰 Итого <b>20 €</b>"
    assert blocks[4] == "<b>Еда</b> 20\n██████████ 100%"
    assert "<b>Еда</b> 1 500\n████░░░░░░ 38%" in blocks[2]
    assert "3 920" not in text
    assert text.endswith("<blockquote expandable>👤 По людям\n"
                         "Азат — 3 000 дин\n"
                         "Лиза — 900 дин · 20 €</blockquote>")


def test_format_month_summary_empty_month():
    assert format_month_summary(month_summary([], "2026-11")) == (
        "📅 <b>Итог за ноябрь 2026</b>\n\nТрат нет")


def test_month_summary_blank_category_and_spender_get_labels():
    rows = [_row(1, "", 10.0, "дин", "")]
    din = month_summary(rows, "2026-11").by_currency["дин"]
    assert din.categories == [("Без категории", 10.0)]
    assert din.spenders == [("Не указан", 10.0)]


def test_convert_same_currency_as_code_and_symbol_needs_no_rate():
    rows = [Row(date=date(2026, 8, 3), month="2026-08", category="Food", amount=5.0,
                currency="EUR", who="Ann", comment=""),
            Row(date=date(2026, 8, 4), month="2026-08", category="Food", amount=2.5,
                currency="€", who="Ann", comment="")]
    text = format_period_stats(rows, H, month="2026-08", convert_to="€",
                               get_rate=lambda *_: None)
    assert "💰 Итого <b>7,50 €</b>" in text
    assert "💱 Курс" not in text


# Large expense (issue #87): every stats request leaves marked rows out,
# «🔶 Только крупные» (large=True) takes only them.
LARGE_ROWS = [
    *ROWS,
    Row(
        date=date(2026, 8, 7), month="2026-08", category="Travel",
        amount=2000.0, currency="€", who="Azat", comment="tickets", large=True,
    ),
]


def test_filter_rows_leaves_large_out_by_default():
    assert [r.comment for r in filter_rows(LARGE_ROWS, month="2026-08")] == ["a", "b"]


def test_filter_rows_only_large():
    out = filter_rows(LARGE_ROWS, month="2026-08", large=True)
    assert [r.comment for r in out] == ["tickets"]


def test_period_stats_leave_large_out_but_list_them_collapsed():
    text = format_period_stats(LARGE_ROWS, H, month="2026-08")
    assert "💰 Итого <b>20 €</b>" in text
    collapsed = text[text.index("<blockquote expandable>"):]
    assert "🔶 Крупные (не в итоге)\ntickets — 2 000 €" in collapsed


def test_period_stats_only_large_same_period_and_conversion():
    text = format_period_stats(LARGE_ROWS, H, month="2026-08", large=True, convert_to="дин",
                               get_rate=lambda src, dst: 117.0)
    assert "💰 Итого <b>234 000 дин</b>" in text
    assert "Крупные (не в итоге)" not in text
    assert "2 000 € → 234 000 дин (курс 117)" in text


def test_period_stats_only_large_says_there_are_none():
    text = format_period_stats(LARGE_ROWS, H, month="2026-07", large=True)
    assert text == f"{H}\n\n🔶 Крупных трат за этот период нет"


def test_last_n_leaves_large_out_by_default():
    assert [r.comment for r in last_n(LARGE_ROWS, n=1)] == ["c"]


def test_format_last_n_only_large():
    text = format_last_n(LARGE_ROWS, n=3, large=True)
    assert text.startswith("🔶 <b>Последние крупные траты</b>\n") and "tickets" in text
    assert format_last_n(ROWS, n=3, large=True) == "📭 Крупных трат нет"
