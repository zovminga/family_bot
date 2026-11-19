#!/usr/bin/env python3
"""Expense analytics over parsed rows (no I/O).

Callers load rows via SheetsClient + sheet_data.parse_rows and pass an
exchange-rate function (fx.rate in the bot) when converting.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Callable, Optional

from family_bot.currency import format_amount
from family_bot.sheet_data import DATE_FMT, Row

GetRate = Callable[[str, str], Optional[float]]
NO_DATA = "Нет данных 🤷"
TOP_CATEGORIES = 3
NO_CATEGORY = "Без категории"
NO_SPENDER = "Не указан"
MONTH_NAMES = ("Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль",
               "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь")


def unique_currencies(rows: list[Row], fallback: Optional[list[str]] = None) -> list[str]:
    """Sorted distinct currency symbols from parsed rows."""
    currencies = sorted({r.currency for r in rows if r.currency})
    if currencies:
        return currencies
    return list(fallback or [])


def last_n(
    rows: list[Row],
    n: int = 3,
    category: Optional[str] = None,
) -> list[Row]:
    """Return the last N expenses (newest first), optionally filtered by category."""
    filtered = rows
    if category and category != "All":
        filtered = [r for r in rows if r.category == category]
    if not filtered:
        return []
    return list(reversed(filtered[-n:]))


def format_last_n(
    rows: list[Row],
    n: int = 3,
    category: Optional[str] = None,
) -> str:
    """Human-readable last-N listing for Telegram."""
    records = last_n(rows, n=n, category=category)
    if not records:
        if category and category != "All":
            return f"📭 Нет записей в категории «{category}»"
        return "📭 Записей нет"

    lines = [f"📋 Последние записи ({len(records)}):\n"]
    for i, record in enumerate(records, 1):
        comment_text = f" ({record.comment})" if record.comment else ""
        lines.append(
            f"{i}. 📅 {record.date_text or '?'} | {record.category or '?'} | "
            f"{format_amount(record.amount, record.currency or '?')} | "
            f"👤 {record.who or '?'}{comment_text}"
        )
    return "\n".join(lines)


def _parse_date(value: str) -> Optional[date]:
    try:
        return datetime.strptime(value, DATE_FMT).date()
    except (TypeError, ValueError):
        return None


def filter_rows(
    rows: list[Row],
    *,
    category: str = "All",
    month: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> list[Row]:
    """Filter parsed expenses by category and period."""
    out = list(rows)
    if month:
        out = [r for r in out if r.month == month]
    elif date_from and date_to:
        start = _parse_date(date_from)
        end = _parse_date(date_to)
        if start is None or end is None:
            return []
        out = [r for r in out if r.date is not None and start <= r.date <= end]
    if category != "All":
        out = [r for r in out if r.category == category]
    return out


def aggregate(
    rows: list[Row],
    *,
    category: str = "All",
    month: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    group_by_currency: bool = True,
    convert_to_currency: Optional[str] = None,
    get_rate: Optional[GetRate] = None,
) -> tuple[str, dict]:
    """Aggregate filtered expenses into Telegram text + conversion details.

    get_rate(from_currency, to_currency) -> rate | None; required when
    convert_to_currency is set.
    """
    conversion_details: dict = {}

    filtered = filter_rows(
        rows,
        category=category,
        month=month,
        date_from=date_from,
        date_to=date_to,
    )
    if not filtered:
        return NO_DATA, {}

    # Working copy of amounts/currencies for optional conversion.
    working: list[dict[str, Any]] = [
        {"amount": r.amount, "currency": r.currency}
        for r in filtered
    ]

    if convert_to_currency:
        if get_rate is None:
            raise ValueError("get_rate is required to convert currencies")
        originals = {w["currency"] for w in working}
        for currency in originals:
            if currency == convert_to_currency:
                continue
            rate = get_rate(currency, convert_to_currency)
            if rate is None:
                return (
                    f"❌ Не удалось получить курс {currency} → {convert_to_currency}",
                    {},
                )
            original_amount = sum(w["amount"] for w in working if w["currency"] == currency)
            for w in working:
                if w["currency"] == currency:
                    w["amount"] *= rate
                    w["currency"] = convert_to_currency
            conversion_details[currency] = {
                "rate": rate,
                "original_amount": original_amount,
                "converted_amount": original_amount * rate,
            }

    if group_by_currency:
        totals: dict[str, float] = {}
        for w in working:
            totals[w["currency"]] = totals.get(w["currency"], 0.0) + w["amount"]
        lines = [format_amount(amt, cur) for cur, amt in totals.items()]
        return ("\n".join(lines) if lines else NO_DATA), conversion_details

    total = sum(w["amount"] for w in working)
    currency = (
        convert_to_currency
        if convert_to_currency
        else (working[0]["currency"] if working else "?")
    )
    return f"💰 Итого: {format_amount(total, currency)}", conversion_details


@dataclass(frozen=True)
class CurrencySummary:
    """
    Month totals in one Currency (never mixed with other Currencies).

    Parameters
    ----------
    total : float
        Sum of all expenses in this Currency.
    categories : list[tuple[str, float]]
        (Category, sum), largest first.
    spenders : list[tuple[str, float]]
        (Spender, sum), largest first.
    """

    total: float
    categories: list[tuple[str, float]]
    spenders: list[tuple[str, float]]


@dataclass(frozen=True)
class MonthSummary:
    """
    One month of expenses, split by Currency.

    Parameters
    ----------
    month : str
        ``YYYY-MM``.
    by_currency : dict[str, CurrencySummary]
        Currency → its totals; the Currency with the most expenses comes first.
    """

    month: str
    by_currency: dict[str, CurrencySummary]


def _ranked(sums: dict[str, float]) -> list[tuple[str, float]]:
    return sorted(sums.items(), key=lambda item: (-item[1], item[0]))


def month_summary(rows: list[Row], month: str) -> MonthSummary:
    """
    Total, Category and Spender sums for one month, per Currency.

    Parameters
    ----------
    rows : list[Row]
        Parsed Data rows.
    month : str
        ``YYYY-MM`` (``MONTH_FMT``), matched against ``Row.month``.

    Returns
    -------
    MonthSummary
        Empty ``by_currency`` when the month has no expenses.
    """
    month_rows = [r for r in rows if r.month == month]
    counts = Counter(r.currency for r in month_rows)
    by_currency: dict[str, CurrencySummary] = {}
    for cur, _ in counts.most_common():
        categories: dict[str, float] = defaultdict(float)
        spenders: dict[str, float] = defaultdict(float)
        for r in month_rows:
            if r.currency == cur:
                categories[r.category or NO_CATEGORY] += r.amount
                spenders[r.who or NO_SPENDER] += r.amount
        by_currency[cur] = CurrencySummary(
            total=sum(categories.values()),
            categories=_ranked(categories),
            spenders=_ranked(spenders),
        )
    return MonthSummary(month=month, by_currency=by_currency)


def month_title(month: str) -> str:
    """«Ноябрь 2026» for ``2026-11``."""
    year, number = month.split("-")
    return f"{MONTH_NAMES[int(number) - 1]} {year}"


def format_month_summary(summary: MonthSummary, top: int = TOP_CATEGORIES) -> str:
    """
    Telegram text for a month summary: total, top Categories, Spenders.

    Each Currency gets its own block; amounts in different Currencies are
    never added together.

    Parameters
    ----------
    summary : MonthSummary
        Result of ``month_summary``.
    top : int
        How many Categories to list per Currency.

    Returns
    -------
    str
        Message text.
    """
    lines = [f"📅 {month_title(summary.month)}"]
    if not summary.by_currency:
        return "\n".join([*lines, NO_DATA])

    lines.append("💰 Итого: " + " · ".join(
        format_amount(s.total, cur) for cur, s in summary.by_currency.items()))
    several = len(summary.by_currency) > 1
    for cur, s in summary.by_currency.items():
        lines.append("")
        if several:
            lines += [f"— {cur} —", ""]
        lines.append("🏷 Топ категорий:")
        lines += [f"{i}. {name} — {format_amount(amount, cur)}"
                  for i, (name, amount) in enumerate(s.categories[:top], 1)]
        lines += ["", "👤 По людям:"]
        lines += [f"{name} — {format_amount(amount, cur)}" for name, amount in s.spenders]
    return "\n".join(lines)
