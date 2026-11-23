#!/usr/bin/env python3
"""Expense analytics over parsed rows (no I/O).

Callers load rows via SheetsClient + sheet_data.parse_rows and pass an
exchange-rate function (fx.rate in the bot) when converting.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Callable, Optional

from family_bot.currency import format_amount, to_iso
from family_bot.look import bar, bold, escape, expandable_quote
from family_bot.sheet_data import DATE_FMT, Row

GetRate = Callable[[str, str], Optional[float]]
NO_DATA = "Нет данных 🤷"
# «23.11» in last-records lines.
SHORT_DATE_FMT = "%d.%m"
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


def _record_line(record: Row) -> str:
    """«<b>Кофе — 350 дин</b> · Еда · 23.11 · Аня»; without a comment the Category leads."""
    what = record.comment or record.category or NO_CATEGORY
    details = [record.category] if record.comment else []
    shown_date = record.date.strftime(SHORT_DATE_FMT) if record.date else ""
    details += [shown_date, record.who]
    tail = " · ".join(d for d in details if d)
    head = bold(f"{what} — {format_amount(record.amount, record.currency or '?')}")
    return f"{head} · {escape(tail)}" if tail else head


def format_last_n(
    rows: list[Row],
    n: int = 3,
    category: Optional[str] = None,
    large: bool = False,
) -> str:
    """
    Last-N listing as Telegram HTML (human text escaped).

    Parameters
    ----------
    rows : list[Row]
        Parsed Data rows.
    n : int
        How many records.
    category : Optional[str]
        One Category, or None / "All" for every one.
    large : bool
        Only Large expenses (else they are left out).

    Returns
    -------
    str
        A bold header (🔶 for Large expenses), then one line per record:
        «<b>Кофе — 350 дин</b> · Еда · 23.11 · Аня».
    """
    records = last_n(rows, n=n, category=category, large=large)
    one_category = bool(category and category != "All")
    if not records:
        if large:
            return NO_LARGE_RECORDS
        if one_category:
            return escape(f"📭 Нет записей в категории «{category}»")
        return "📭 Записей нет"

    if large:
        header = "🔶 " + bold("Последние крупные траты")
    else:
        header = "📜 " + bold(f"Последние записи · {category}" if one_category
                             else "Последние записи")
    return "\n".join([header, *(_record_line(r) for r in records)])


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
    return MonthSummary(month=month, by_currency=_by_currency(month_rows), large=large)


def _by_currency(rows: list[Row]) -> dict[str, CurrencySummary]:
    """Category and Spender sums per Currency; the Currency with the most rows first."""
    counts = Counter(r.currency for r in rows)
    by_currency: dict[str, CurrencySummary] = {}
    for cur, _ in counts.most_common():
        categories: dict[str, float] = defaultdict(float)
        spenders: dict[str, float] = defaultdict(float)
        for r in rows:
            if r.currency == cur:
                categories[r.category or NO_CATEGORY] += r.amount
                spenders[r.who or NO_SPENDER] += r.amount
        by_currency[cur] = CurrencySummary(
            total=sum(categories.values()),
            categories=_ranked(categories),
            spenders=_ranked(spenders),
        )
    return by_currency


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


def _details(by_currency: dict[str, CurrencySummary], large: tuple[Row, ...],
             extra: tuple[str, ...] = ()) -> str:
    """
    Body of the collapsed block: Spenders, then Large expenses, then ``extra``.

    A Spender with expenses in several Currencies gets one line with the
    amounts joined by « · »; they are never added together.
    """
    spenders: dict[str, list[str]] = {}
    for cur, sums in by_currency.items():
        for name, amount in sums.spenders:
            spenders.setdefault(name, []).append(format_amount(amount, cur))
    parts: list[str] = []
    if spenders:
        parts.append("\n".join(["👤 По людям", *(
            escape(f"{name} — {' · '.join(amounts)}") for name, amounts in spenders.items())]))
    if large:
        # A row without a comment is named by its Category.
        parts.append("\n".join(["🔶 Крупные (не в итоге)", *(
            escape(f"{r.comment or r.category or NO_CATEGORY} — "
                   f"{format_amount(r.amount, r.currency)}") for r in large)]))
    parts += extra
    return "\n\n".join(parts)


def _format_summary(header: str, by_currency: dict[str, CurrencySummary],
                    large: tuple[Row, ...], empty: str, extra: tuple[str, ...] = ()) -> str:
    """Header, a block per Currency (or ``empty``), then the collapsed details."""
    blocks = [header]
    if by_currency:
        blocks += ["\n".join(_currency_block(cur, sums)) for cur, sums in by_currency.items()]
    else:
        blocks.append(empty)
    details = _details(by_currency, large, extra)
    if details:
        blocks.append(expandable_quote(details))
    return "\n\n".join(blocks)


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
    return _format_summary(
        _month_header(summary.month), summary.by_currency, summary.large, NO_EXPENSES)


def convert_rows(rows: list[Row], target: str,
                 get_rate: GetRate) -> tuple[list[Row], dict[str, float], list[str]]:
    """
    Rows with every amount in ``target``.

    Parameters
    ----------
    rows : list[Row]
        Rows to convert.
    target : str
        Currency as stored («дин», «€», an ISO code).
    get_rate : GetRate
        ``get_rate(from, to)`` -> rate, or None when unknown.

    Returns
    -------
    tuple[list[Row], dict[str, float], list[str]]
        Converted rows, the rate of each converted Currency, and the
        Currencies without a rate (then nothing is converted). A Currency
        that is ``target`` under another name («EUR» for «€») is relabelled
        without a rate.
    """
    target_iso = to_iso(target)
    relabelled = [replace(r, currency=target) if to_iso(r.currency) == target_iso else r
                  for r in rows]
    found = {c: get_rate(c, target) for c in sorted({r.currency for r in relabelled} - {target})}
    missing = [c for c, rate in found.items() if rate is None]
    rates = {c: rate for c, rate in found.items() if rate is not None}
    if missing:
        return rows, {}, missing
    converted = [r if r.currency == target
                 else replace(r, amount=r.amount * rates[r.currency], currency=target)
                 for r in relabelled]
    return converted, rates, []


def _rate_lines(rows: list[Row], target: str, rates: dict[str, float]) -> str:
    """«💱 Курс», then «10 € → 1 170 дин (курс 117)» for each converted Currency."""
    lines = ["💱 Курс"]
    for cur, rate in rates.items():
        original = sum(r.amount for r in rows if r.currency == cur)
        # Significant digits, so a rate into a costly Currency (дин → KWD) is not 0,0000.
        shown_rate = f"{rate:.6g}".replace(".", ",")
        lines.append(escape(f"{format_amount(original, cur)} → "
                            f"{format_amount(original * rate, target)} (курс {shown_rate})"))
    return "\n".join(lines)


def format_period_stats(
    rows: list[Row],
    header: str,
    *,
    category: str = "All",
    month: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    large: bool = False,
    convert_to: Optional[str] = None,
    get_rate: Optional[GetRate] = None,
) -> str:
    """
    Statistics answer for a month or a period, in the month summary look.

    Parameters
    ----------
    rows : list[Row]
        Parsed Data rows.
    header : str
        First line (HTML).
    category, month, date_from, date_to, large
        Filters, as in ``filter_rows``.
    convert_to : Optional[str]
        Currency to convert every amount into; None keeps a block per Currency.
    get_rate : Optional[GetRate]
        Rate source; required with ``convert_to``.

    Returns
    -------
    str
        Telegram HTML: header, a block per Currency with Category share bars,
        then a collapsed block with Spenders, the period's Large expenses
        (when they are not the subject) and the conversion rates.
    """
    filtered = filter_rows(rows, category=category, month=month,
                           date_from=date_from, date_to=date_to, large=large)
    if not filtered:
        return f"{header}\n\n{NO_LARGE if large else NO_DATA}"
    aside: tuple[Row, ...] = () if large else tuple(filter_rows(
        rows, category=category, month=month, date_from=date_from, date_to=date_to,
        large=True))
    extra: tuple[str, ...] = ()
    if convert_to:
        if get_rate is None:
            raise ValueError("get_rate is required to convert currencies")
        converted, rates, missing = convert_rows(filtered, convert_to, get_rate)
        if missing:
            # Never sum without a rate: say which Currencies could not be converted.
            return header + "\n\n" + escape(
                f"❌ Нет курса для пересчёта в {convert_to}: {', '.join(missing)}.")
        if rates:
            extra = (_rate_lines(filtered, convert_to, rates),)
        filtered = converted
    return _format_summary(header, _by_currency(filtered), aside, NO_DATA, extra)
