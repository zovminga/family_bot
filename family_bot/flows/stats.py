#!/usr/bin/env python3
"""Statistics conversation flow.

The whole request lives in one ``StatQuery`` under ``user_data[QUERY_KEY]``.
A new query starts at the menu tap and again at the category step, so values
from an earlier request never leak into the next one. Every exit path returns
CHOOSE_ACTION and drops the query.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Optional, TypeVar

from dateutil import parser as dparser
from dateutil.relativedelta import relativedelta
from telegram import Message, ReplyKeyboardMarkup, Update
from telegram.ext import BaseHandler, ContextTypes, MessageHandler, filters

from family_bot import analytics
from family_bot import clock
from family_bot import errors
from family_bot import navigation as nav
from family_bot.conversation_states import (
    CHOOSE_ACTION, STAT_CAT, STAT_TYPE, STAT_DATE_FROM, STAT_DATE_TO, STAT_MONTH,
    STAT_GROUP_CURRENCY, STAT_CONVERT_CURRENCY, STAT_SHOW_DETAILS,
)
from family_bot.sheet_data import DATE_FMT, MONTH_FMT

QUERY_KEY = "stat_query"
ALL_CATEGORIES = "All"
MONTHS_SHOWN = 12
LAST_RECORDS_SHOWN = 3

T = TypeVar("T")


@dataclass
class StatQuery:
    """Parameters of one statistics request."""

    category: str = ALL_CATEGORIES
    month: Optional[str] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    group_by_currency: bool = True
    convert_to: Optional[str] = None
    conversion_details: dict[str, dict[str, float]] = field(default_factory=dict)


def load_query(user_data: dict[str, Any]) -> StatQuery:
    """Return the current query, creating an empty one if there is none."""
    query = user_data.get(QUERY_KEY)
    if not isinstance(query, StatQuery):
        query = StatQuery()
        user_data[QUERY_KEY] = query
    return query


def reset(user_data: dict[str, Any]) -> None:
    """Drop the current statistics query (called on /start and on flow exit)."""
    user_data.pop(QUERY_KEY, None)


def _message(update: Update) -> Message:
    # Every handler here is a text MessageHandler, so the message is always set.
    assert update.message is not None
    return update.message


def _user_data(context: ContextTypes.DEFAULT_TYPE) -> dict[str, Any]:
    assert context.user_data is not None
    return context.user_data


def _keyboard(rows: list[list[str]], one_time: bool = True) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [*rows, nav.home_row()], one_time_keyboard=one_time, resize_keyboard=True)


def _parse_date(text: str) -> str:
    return dparser.parse(text, dayfirst=True).strftime(DATE_FMT)


Source = Callable[[ContextTypes.DEFAULT_TYPE], T]


class StatsFlow:
    """Statistics handlers with their data sources injected.

    Parameters
    ----------
    categories : Source[list[str]]
        Current category list (re-read on every call so /reloadcats applies).
    load_rows : Source[list[dict]]
        Normalized Data-sheet rows.
    fallback_currencies : Source[list[str]]
        Currencies offered when the sheet has none.

    Each source takes the handler's context, so it can read the bot's Config.
    get_rate : analytics.GetRate
        FX rate source for conversion.
    """

    def __init__(
        self,
        *,
        categories: Source[list[str]],
        load_rows: Source[list[dict]],
        fallback_currencies: Source[list[str]],
        get_rate: analytics.GetRate = analytics.http_exchange_rate,
    ) -> None:
        self._categories = categories
        self._load_rows = load_rows
        self._fallback_currencies = fallback_currencies
        self._get_rate = get_rate

    def states(self) -> dict[object, list[BaseHandler]]:
        """ConversationHandler states for this flow."""
        def text(callback: Callable[[Update, Any], Coroutine[Any, Any, int]]) -> list[BaseHandler]:
            return [MessageHandler(filters.TEXT & ~filters.COMMAND, callback)]

        return {
            STAT_CAT: text(self.on_category),
            STAT_TYPE: text(self.on_type),
            STAT_DATE_FROM: text(self.on_date_from),
            STAT_DATE_TO: text(self.on_date_to),
            STAT_MONTH: text(self.on_month),
            STAT_GROUP_CURRENCY: text(self.on_group_currency),
            STAT_CONVERT_CURRENCY: text(self.on_convert_currency),
            STAT_SHOW_DETAILS: text(self.on_show_details),
        }

    async def begin(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Main-menu entry: start a fresh query and ask for the category scope."""
        _user_data(context)[QUERY_KEY] = StatQuery()
        await _message(update).reply_text(
            "📊 Statistics for all categories or specific?",
            reply_markup=_keyboard([["All categories", "Specific category"]]),
        )
        return STAT_CAT

    async def on_category(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """STAT_CAT: all categories, the category list, or a chosen category (starts a new query)."""
        text = _message(update).text or ""
        if nav.is_home_text(text):
            return await nav.go_home(update, context)

        if text == "Specific category":
            await _message(update).reply_text(
                "📂 Choose category:",
                reply_markup=_keyboard([[c] for c in self._categories(context)]),
            )
            return STAT_CAT

        category = ALL_CATEGORIES if text == "All categories" else text
        _user_data(context)[QUERY_KEY] = StatQuery(category=category)
        await _message(update).reply_text(
            f"📊 Statistics type for category '{category}':",
            reply_markup=_keyboard([["📜 Last 3 records", "📅 Custom period", "📆 By months"]]),
        )
        return STAT_TYPE

    async def on_type(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """STAT_TYPE: last records, custom period, or month list."""
        text = _message(update).text or ""
        if nav.is_home_text(text):
            return await nav.go_home(update, context)

        if text == "📜 Last 3 records":
            category = load_query(_user_data(context)).category
            await _message(update).reply_text(
                await asyncio.to_thread(self._last_records, context, category))
            return await nav.go_home(update, context)

        if text == "📅 Custom period":
            await _message(update).reply_text(
                "📅 Enter start date (DD.MM.YYYY):",
                reply_markup=_keyboard([], one_time=False),
            )
            return STAT_DATE_FROM

        if text == "📆 By months":
            first_of_month = clock.now().replace(day=1)
            months = [(first_of_month - relativedelta(months=i)).strftime(MONTH_FMT)
                      for i in range(MONTHS_SHOWN)]
            await _message(update).reply_text(
                "📆 Choose month:", reply_markup=_keyboard([[m] for m in months]))
            return STAT_MONTH

        await _message(update).reply_text("Please choose a valid option")
        return STAT_TYPE

    async def on_date_from(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """STAT_DATE_FROM: parse the period start."""
        text = _message(update).text or ""
        if nav.is_home_text(text):
            return await nav.go_home(update, context)
        try:
            date_from = _parse_date(text)
        except (ValueError, OverflowError):
            await _message(update).reply_text("❌ Cannot parse date, try 13.07.2025")
            return STAT_DATE_FROM
        query = load_query(_user_data(context))
        query.month, query.date_from = None, date_from
        await _message(update).reply_text(
            "📅 Enter end date (DD.MM.YYYY):", reply_markup=_keyboard([], one_time=False))
        return STAT_DATE_TO

    async def on_date_to(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """STAT_DATE_TO: parse the period end, then ask about grouping."""
        text = _message(update).text or ""
        if nav.is_home_text(text):
            return await nav.go_home(update, context)
        try:
            date_to = _parse_date(text)
        except (ValueError, OverflowError):
            await _message(update).reply_text("❌ Cannot parse date, try 13.07.2025")
            return STAT_DATE_TO
        load_query(_user_data(context)).date_to = date_to
        return await self._ask_grouping(update)

    async def on_month(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """STAT_MONTH: store the month, then ask about grouping."""
        text = _message(update).text or ""
        if nav.is_home_text(text):
            return await nav.go_home(update, context)
        query = load_query(_user_data(context))
        query.month, query.date_from, query.date_to = text, None, None
        return await self._ask_grouping(update)

    async def on_group_currency(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """STAT_GROUP_CURRENCY: show grouped result, or ask for a conversion currency."""
        text = _message(update).text or ""
        if nav.is_home_text(text):
            return await nav.go_home(update, context)

        query = load_query(_user_data(context))
        query.group_by_currency = text == "Yes"
        if query.group_by_currency:
            query.convert_to = None
            await self._reply_result(update, context, query)
            return await self._finish(update, context)

        await _message(update).reply_text(
            "💱 Convert all expenses to which currency?",
            reply_markup=_keyboard(
                [[c] for c in await asyncio.to_thread(self._currencies, context)]),
        )
        return STAT_CONVERT_CURRENCY

    async def on_convert_currency(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """STAT_CONVERT_CURRENCY: show converted result; offer details only if there are any."""
        text = _message(update).text or ""
        if nav.is_home_text(text):
            return await nav.go_home(update, context)

        query = load_query(_user_data(context))
        query.convert_to = text
        await self._reply_result(update, context, query)
        if not query.conversion_details:
            return await self._finish(update, context)

        await _message(update).reply_text(
            "Would you like to see conversion details?",
            reply_markup=_keyboard([["Show details", "Done (thanks)"]]),
        )
        return STAT_SHOW_DETAILS

    async def on_show_details(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """STAT_SHOW_DETAILS: optionally show conversion details, then go home."""
        text = _message(update).text or ""
        if nav.is_home_text(text):
            return await nav.go_home(update, context)

        if text == "Show details":
            query = load_query(_user_data(context))
            if query.conversion_details:
                await _message(update).reply_text(_details_text(query))
            else:
                await _message(update).reply_text("No conversion details available")
        return await nav.go_home(update, context)

    async def _ask_grouping(self, update: Update) -> int:
        await _message(update).reply_text(
            "💱 Group by currencies?", reply_markup=_keyboard([["Yes", "No"]]))
        return STAT_GROUP_CURRENCY

    async def _finish(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        reset(_user_data(context))
        await nav.show_main_menu(update, context)
        return CHOOSE_ACTION

    def _currencies(self, context: ContextTypes.DEFAULT_TYPE) -> list[str]:
        try:
            return analytics.unique_currencies(
                self._load_rows(context), fallback=self._fallback_currencies(context))
        except Exception as e:  # noqa: BLE001 - offer fallback currencies
            print(f"Error getting currencies: {e}")
            return self._fallback_currencies(context)

    def _last_records(self, context: ContextTypes.DEFAULT_TYPE, category: str) -> str:
        try:
            return analytics.format_last_n(
                self._load_rows(context), n=LAST_RECORDS_SHOWN,
                category=None if category == ALL_CATEGORIES else category)
        except Exception as e:  # noqa: BLE001 - details go to the log
            errors.log_exception("Last records", e)
            return "❌ Не удалось загрузить записи. Попробуйте ещё раз"

    def _aggregate(self, context: ContextTypes.DEFAULT_TYPE,
                   query: StatQuery) -> tuple[str, dict]:
        """Load rows and compute the statistics text (blocking: Sheets + FX)."""
        return analytics.aggregate(
            self._load_rows(context),
            category=query.category,
            month=query.month,
            date_from=query.date_from,
            date_to=query.date_to,
            group_by_currency=query.group_by_currency,
            convert_to_currency=query.convert_to,
            get_rate=self._get_rate,
        )

    async def _reply_result(self, update: Update, context: ContextTypes.DEFAULT_TYPE,
                            query: StatQuery) -> None:
        """Send the statistics text and store conversion details in the query."""
        if query.month:
            period_text = f"for {query.month}"
        elif query.date_from and query.date_to:
            period_text = f"from {query.date_from} to {query.date_to}"
        else:
            await _message(update).reply_text(
                f"📊 Statistics , category '{query.category}':\n❌ Error: period not specified")
            return

        # Sheets read and FX requests block, so they run in a worker thread.
        stats, query.conversion_details = await asyncio.to_thread(
            self._aggregate, context, query)
        suffix = f" (in {query.convert_to})" if query.convert_to else ""
        await _message(update).reply_text(
            f"📊 Statistics {period_text}, category '{query.category}'{suffix}:\n{stats}")


def _details_text(query: StatQuery) -> str:
    lines = ["💱 Conversion details:\n"]
    for from_cur, details in query.conversion_details.items():
        lines.append(
            f"{from_cur} → {query.convert_to}:\n"
            f"  Rate: {details['rate']:.4f}\n"
            f"  Original: {details['original_amount']:,.2f} {from_cur}\n"
            f"  Converted: {details['converted_amount']:,.2f} {query.convert_to}\n"
        )
    return "\n".join(lines)
