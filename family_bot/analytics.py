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

from family_bot.currency import format_amount, to_iso
from family_bot.look import bar, bold, escape, expandable_quote
from family_bot.sheet_data import DATE_FMT, Row

GetRate = Callable[[str, str], Optional[float]]
NO_DATA = "Нет данных 🤷"
NO_EXPENSES = "Трат нет"
NO_LARGE = "🔶 Крупных трат за этот период нет"
NO_LARGE_RECORDS = "📭 Крупных трат нет"
NO_CATEGORY = "Без категории"
NO_SPENDER = "Не указан"
MONTH_NAMES = ("Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль",
               "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь")


def last_n(
    rows: list[Row],
    n: int = 3,
    category: Optional[str] = None,
    large: bool = False,
) -> list[Row]:
    """Return the last N expenses (newest first), optionally filtered by category.

    Large expenses are left out; ``large=True`` takes only them.
    """
    filtered = [r for r in rows if r.large == large]
    if category and category != "All":
        filtered = [r for r in filtered if r.category == category]
    if not filtered:
        return []
    return list(reversed(filtered[-n:]))


def format_last_n(
    rows: list[Row],
    n: int = 3,
    category: Optional[str] = None,
    large: bool = False,
) -> str:
    """Last-N listing as Telegram HTML (human text escaped)."""
    records = last_n(rows, n=n, category=category, large=large)
    if not records:
        if large:
            return NO_LARGE_RECORDS
        if category and category != "All":
            return escape(f"📭 Нет записей в категории «{category}»")
        return "📭 Записей нет"

    lines = [f"📋 Последние записи ({len(records)}):\n"]
    for i, record in enumerate(records, 1):
        comment_text = f" ({record.comment})" if record.comment else ""
        lines.append(
            f"{i}. 📅 {record.date_text or '?'} | {record.category or '?'} | "
            f"{format_amount(record.amount, record.currency or '?')} | "
            f"👤 {record.who or '?'}{comment_text}"
        )
    return escape("\n".join(lines))


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
    large: bool = False,
) -> list[Row]:
    """Filter parsed expenses by category and period.

    Large expenses are left out; ``large=True`` takes only them.
    """
    out = [r for r in rows if r.large == large]
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
    large: bool = False,
) -> tuple[str, dict]:
    """Aggregate filtered expenses into Telegram HTML text + conversion details.

    get_rate(from_currency, to_currency) -> rate | None; required when
    convert_to_currency is set. Large expenses are left out; ``large=True``
    takes only them.
    """
    conversion_details: dict = {}

    filtered = filter_rows(
        rows,
        category=category,
        month=month,
        date_from=date_from,
        date_to=date_to,
        large=large,
    )
    if not filtered:
        return (NO_LARGE if large else NO_DATA), {}

    # Working copy of amounts/currencies for optional conversion.
    working: list[dict[str, Any]] = [
        {"amount": r.amount, "currency": r.currency}
        for r in filtered
    ]

    if convert_to_currency:
        if get_rate is None:
            raise ValueError("get_rate is required to convert currencies")
        target_iso = to_iso(convert_to_currency)
        for w in working:
            # «EUR» and «€» are one Currency: relabel, no rate needed.
            if to_iso(w["currency"]) == target_iso:
                w["currency"] = convert_to_currency
        originals = sorted({w["currency"] for w in working} - {convert_to_currency})
        found = {c: get_rate(c, convert_to_currency) for c in originals}
        missing = [c for c, rate in found.items() if rate is None]
        if missing:
            # Never sum without a rate: say which Currencies could not be converted.
            return (
                escape(f"❌ Нет курса для пересчёта в {convert_to_currency}: "
                       f"{', '.join(missing)}."),
                {},
            )
        rates = {c: rate for c, rate in found.items() if rate is not None}
        for currency, rate in rates.items():
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
        return (escape("\n".join(lines)) if lines else NO_DATA), conversion_details

    total = sum(w["amount"] for w in working)
    currency = (
        convert_to_currency
        if convert_to_currency
        else (working[0]["currency"] if working else "?")
    )
    return escape(f"💰 Итого: {format_amount(total, currency)}"), conversion_details


@dataclass(frozen=True)
class CurrencySummary:
    """
    Month totals in one Currency (never mixed with other Currencies).

    Attributes
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

    Attributes
    ----------
    month : str
        ``YYYY-MM``.
    by_currency : dict[str, CurrencySummary]
        Currency → its totals without Large expenses; the Currency with the
        most expenses comes first.
    large : tuple[Row, ...]
        The month's Large expenses, in sheet order; not in ``by_currency``.
    """

    month: str
    by_currency: dict[str, CurrencySummary]
    large: tuple[Row, ...] = ()


def _ranked(sums: dict[str, float]) -> list[tuple[str, float]]:
    return sorted(sums.items(), key=lambda item: (-item[1], item[0]))


def month_summary(rows: list[Row], month: str) -> MonthSummary:
    """
    Total, Category and Spender sums for one month, per Currency.

    Large expenses are left out of the sums and returned apart.

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
    month_rows = [r for r in rows if r.month == month and not r.large]
    large = tuple(r for r in rows if r.month == month and r.large)
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
    return MonthSummary(month=month, by_currency=by_currency, large=large)


def month_title(month: str) -> str:
    """«Ноябрь 2026» for ``2026-11``."""
    year, number = month.split("-")
    return f"{MONTH_NAMES[int(number) - 1]} {year}"


def _month_header(month: str) -> str:
    """«📅 <b>Итог за ноябрь 2026</b>» for ``2026-11``."""
    year, number = month.split("-")
    return "📅 " + bold(f"Итог за {MONTH_NAMES[int(number) - 1].lower()} {year}")


def _percent(amount: float, total: float) -> int:
    """Share of ``total`` in whole percent, rounded half up; 0 for a zero total."""
    return int(amount / total * 100 + 0.5) if total else 0


def _currency_block(cur: str, sums: CurrencySummary) -> list[str]:
    """Total in one Currency, then each Category with its share bar."""
    lines = ["💰 Итого " + bold(format_amount(sums.total, cur)), ""]
    for name, amount in sums.categories:
        share = amount / sums.total if sums.total else 0.0
        lines += [f"{bold(name)} {format_amount(amount)}",
                  f"{bar(share)} {_percent(amount, sums.total)}%"]
    return lines


def _details(summary: MonthSummary) -> str:
    """
    Body of the collapsed block: Spenders, then Large expenses.

    A Spender with expenses in several Currencies gets one line with the
    amounts joined by « · »; they are never added together.
    """
    spenders: dict[str, list[str]] = {}
    for cur, sums in summary.by_currency.items():
        for name, amount in sums.spenders:
            spenders.setdefault(name, []).append(format_amount(amount, cur))
    parts: list[str] = []
    if spenders:
        parts.append("\n".join(["👤 По людям", *(
            escape(f"{name} — {' · '.join(amounts)}") for name, amounts in spenders.items())]))
    if summary.large:
        # A row without a comment is named by its Category.
        parts.append("\n".join(["🔶 Крупные (не в итоге)", *(
            escape(f"{r.comment or r.category or NO_CATEGORY} — "
                   f"{format_amount(r.amount, r.currency)}") for r in summary.large)]))
    return "\n\n".join(parts)


def format_month_summary(summary: MonthSummary) -> str:
    """
    Telegram HTML for a month summary («📅 Этот месяц», the 1st-of-month post).

    Each Currency gets its own block: bold total, then every Category in bold
    with its amount and a share bar with the percent of that Currency's
    total. Amounts in different Currencies are never added together.
    Spenders and Large expenses go into one collapsed block at the end.

    Parameters
    ----------
    summary : MonthSummary
        Result of ``month_summary``.

    Returns
    -------
    str
        Message text (HTML, human text escaped).
    """
    blocks = [_month_header(summary.month)]
    if summary.by_currency:
        blocks += ["\n".join(_currency_block(cur, sums))
                   for cur, sums in summary.by_currency.items()]
    else:
        blocks.append(NO_EXPENSES)
    details = _details(summary)
    if details:
        blocks.append(expandable_quote(details))
    return "\n\n".join(blocks)
