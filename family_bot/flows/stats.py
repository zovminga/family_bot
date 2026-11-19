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
from telegram.ext import BaseHandler, ContextTypes, MessageHandler

from family_bot import analytics
from family_bot import clock
from family_bot import currency
from family_bot import errors
from family_bot import navigation as nav
from family_bot.conversation_states import State
from family_bot.sheet_data import DATE_FMT, MONTH_FMT, Row

QUERY_KEY = "stat_query"
ALL_CATEGORIES = "All"
MONTHS_SHOWN = 12
LAST_RECORDS_SHOWN = 3

# Button texts; the handlers below match on them.
ALL_BUTTON = "Все категории"
ONE_BUTTON = "Одна категория"
LAST_BUTTON = f"📜 Последние {LAST_RECORDS_SHOWN} записи"
PERIOD_BUTTON = "📅 Свой период"
MONTHS_BUTTON = "📆 По месяцам"
YES_BUTTON = "Да"
NO_BUTTON = "Нет"
DETAILS_BUTTON = "Показать детали"
DONE_BUTTON = "Готово"
BAD_DATE = "❌ Не понял дату. Пример: 13.07.2025"

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
    load_rows : Source[list[Row]]
        Parsed Data-sheet rows.
    fallback_currencies : Source[list[str]]
        Currencies offered when the sheet has none.

    Each source takes the handler's context, so it can read the bot's Config.
    get_rate : analytics.GetRate
        FX rate source for conversion (``fx.rate`` in the bot).
    """

    def __init__(
        self,
        *,
        categories: Source[list[str]],
        load_rows: Source[list[Row]],
        fallback_currencies: Source[list[str]],
        get_rate: analytics.GetRate,
    ) -> None:
        self._categories = categories
        self._load_rows = load_rows
        self._fallback_currencies = fallback_currencies
        self._get_rate = get_rate

    def states(self) -> dict[object, list[BaseHandler]]:
        """ConversationHandler states for this flow."""
        def text(callback: Callable[[Update, Any], Coroutine[Any, Any, int]]) -> list[BaseHandler]:
            return [MessageHandler(nav.INPUT_TEXT, callback)]

        return {
            State.STAT_CAT: text(self.on_category),
            State.STAT_TYPE: text(self.on_type),
            State.STAT_DATE_FROM: text(self.on_date_from),
            State.STAT_DATE_TO: text(self.on_date_to),
            State.STAT_MONTH: text(self.on_month),
            State.STAT_GROUP_CURRENCY: text(self.on_group_currency),
            State.STAT_CONVERT_CURRENCY: text(self.on_convert_currency),
            State.STAT_SHOW_DETAILS: text(self.on_show_details),
        }

    async def begin(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Main-menu entry: start a fresh query and ask for the category scope."""
        _user_data(context)[QUERY_KEY] = StatQuery()
        await _message(update).reply_text(
            "📊 Статистика по всем категориям или по одной?",
            reply_markup=_keyboard([[ALL_BUTTON, ONE_BUTTON]]),
        )
        return State.STAT_CAT

    async def this_month(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Main-menu entry: current month summary in one tap; stays on the menu."""
        month = clock.now().strftime(MONTH_FMT)
        await _message(update).reply_text(
            await asyncio.to_thread(self._month_summary, context, month))
        return State.CHOOSE_ACTION

    async def on_category(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """STAT_CAT: all categories, the category list, or a chosen category (starts a new query)."""
        text = _message(update).text or ""

        if text == ONE_BUTTON:
            await _message(update).reply_text(
                "📂 Выберите категорию:",
                reply_markup=_keyboard([[c] for c in self._categories(context)]),
            )
            return State.STAT_CAT

        category = ALL_CATEGORIES if text == ALL_BUTTON else text
        _user_data(context)[QUERY_KEY] = StatQuery(category=category)
        await _message(update).reply_text(
            f"📊 Что показать: {_category_label(category)}?",
            reply_markup=_keyboard([[LAST_BUTTON, PERIOD_BUTTON, MONTHS_BUTTON]]),
        )
        return State.STAT_TYPE

    async def on_type(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """STAT_TYPE: last records, custom period, or month list."""
        text = _message(update).text or ""

        if text == LAST_BUTTON:
            category = load_query(_user_data(context)).category
            await _message(update).reply_text(
                await asyncio.to_thread(self._last_records, context, category))
            return await nav.go_home(update, context)

        if text == PERIOD_BUTTON:
            await _message(update).reply_text(
                "📅 Дата начала (ДД.ММ.ГГГГ):",
                reply_markup=_keyboard([], one_time=False),
            )
            return State.STAT_DATE_FROM

        if text == MONTHS_BUTTON:
            first_of_month = clock.now().replace(day=1)
            months = [(first_of_month - relativedelta(months=i)).strftime(MONTH_FMT)
                      for i in range(MONTHS_SHOWN)]
            await _message(update).reply_text(
                "📆 Выберите месяц:", reply_markup=_keyboard([[m] for m in months]))
            return State.STAT_MONTH

        await _message(update).reply_text("Выберите вариант с кнопки")
        return State.STAT_TYPE

    async def on_date_from(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """STAT_DATE_FROM: parse the period start."""
        text = _message(update).text or ""
        try:
            date_from = _parse_date(text)
        except (ValueError, OverflowError):
            await _message(update).reply_text(BAD_DATE)
            return State.STAT_DATE_FROM
        query = load_query(_user_data(context))
        query.month, query.date_from = None, date_from
        await _message(update).reply_text(
            "📅 Дата конца (ДД.ММ.ГГГГ):", reply_markup=_keyboard([], one_time=False))
        return State.STAT_DATE_TO

    async def on_date_to(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """STAT_DATE_TO: parse the period end, then ask about grouping."""
        text = _message(update).text or ""
        try:
            date_to = _parse_date(text)
        except (ValueError, OverflowError):
            await _message(update).reply_text(BAD_DATE)
            return State.STAT_DATE_TO
        load_query(_user_data(context)).date_to = date_to
        return await self._ask_grouping(update)

    async def on_month(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """STAT_MONTH: store the month, then ask about grouping."""
        text = _message(update).text or ""
        query = load_query(_user_data(context))
        query.month, query.date_from, query.date_to = text, None, None
        return await self._ask_grouping(update)

    async def on_group_currency(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """STAT_GROUP_CURRENCY: show grouped result, or ask for a conversion currency."""
        text = _message(update).text or ""

        query = load_query(_user_data(context))
        query.group_by_currency = text == YES_BUTTON
        if query.group_by_currency:
            query.convert_to = None
            await self._reply_result(update, context, query)
            return await self._finish(update, context)

        await _message(update).reply_text(
            "💱 В какую валюту пересчитать все траты?",
            reply_markup=_keyboard(
                [[c] for c in await asyncio.to_thread(self._currencies, context)]),
        )
        return State.STAT_CONVERT_CURRENCY

    async def on_convert_currency(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """STAT_CONVERT_CURRENCY: show converted result; offer details only if there are any."""
        text = _message(update).text or ""

        query = load_query(_user_data(context))
        query.convert_to = text
        await self._reply_result(update, context, query)
        if not query.conversion_details:
            return await self._finish(update, context)

        await _message(update).reply_text(
            "Показать детали пересчёта?",
            reply_markup=_keyboard([[DETAILS_BUTTON, DONE_BUTTON]]),
        )
        return State.STAT_SHOW_DETAILS

    async def on_show_details(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """STAT_SHOW_DETAILS: optionally show conversion details, then go home."""
        text = _message(update).text or ""

        if text == DETAILS_BUTTON:
            query = load_query(_user_data(context))
            if query.conversion_details:
                await _message(update).reply_text(_details_text(query))
            else:
                await _message(update).reply_text("Деталей пересчёта нет")
        return await nav.go_home(update, context)

    async def _ask_grouping(self, update: Update) -> int:
        await _message(update).reply_text(
            "💱 Разбить по валютам?", reply_markup=_keyboard([[YES_BUTTON, NO_BUTTON]]))
        return State.STAT_GROUP_CURRENCY

    async def _finish(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        reset(_user_data(context))
        await nav.show_main_menu(update, context)
        return State.CHOOSE_ACTION

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

    def _month_summary(self, context: ContextTypes.DEFAULT_TYPE, month: str) -> str:
        try:
            return analytics.format_month_summary(
                analytics.month_summary(self._load_rows(context), month))
        except Exception as e:  # noqa: BLE001 - details go to the log
            errors.log_exception("Month summary", e)
            return "❌ Не удалось загрузить траты. Попробуйте ещё раз"

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
            period_text = f"за {query.month}"
        elif query.date_from and query.date_to:
            period_text = f"с {query.date_from} по {query.date_to}"
        else:
            await _message(update).reply_text(
                f"📊 Статистика, {_category_label(query.category)}:\n❌ Не задан период")
            return

        # Sheets read and FX requests block, so they run in a worker thread.
        stats, query.conversion_details = await asyncio.to_thread(
            self._aggregate, context, query)
        suffix = f" (в {query.convert_to})" if query.convert_to else ""
        await _message(update).reply_text(
            f"📊 Статистика {period_text}, {_category_label(query.category)}{suffix}:\n{stats}")


def _category_label(category: str) -> str:
    """
    Category scope as shown in prompts and result headers.

    Parameters
    ----------
    category : str
        Category name, or ``ALL_CATEGORIES``.

    Returns
    -------
    str
        «все категории» or «категория «Еда»».
    """
    return "все категории" if category == ALL_CATEGORIES else f"категория «{category}»"


def _details_text(query: StatQuery) -> str:
    lines = ["💱 Детали пересчёта:\n"]
    for from_cur, details in query.conversion_details.items():
        rate = f"{details['rate']:.4f}".replace(".", ",")
        lines.append(
            f"{from_cur} → {query.convert_to}:\n"
            f"  Курс: {rate}\n"
            f"  Было: {currency.format_amount(details['original_amount'], from_cur)}\n"
            f"  Стало: {currency.format_amount(details['converted_amount'], query.convert_to)}\n"
        )
    return "\n".join(lines)
