#!/usr/bin/env python3
"""Statistics conversation flow.

The whole request lives in one ``StatQuery`` under ``user_data[QUERY_KEY]``.
A new query starts at the menu tap and again at the category step, so values
from an earlier request (the «🔶 Только крупные» choice too) never leak into the
next one. Large expenses are left out of every request except «🔶 Только
крупные», which takes the same path over Large expenses only. Every exit path returns
CHOOSE_ACTION and drops the query.

The Conversion target is chosen with the shared Currency picker
(``currency_picker``), like manual input: Recent currencies, «🌍 Другая» with
every Currency of the rate service, or typed code / name. The chosen Currency
goes to the User's Recent currencies.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Optional, TypeVar

from dateutil import parser as dparser
from dateutil.relativedelta import relativedelta
from telegram import (InlineKeyboardButton, InlineKeyboardMarkup, Message,
                      ReplyKeyboardMarkup, Update)
from telegram.ext import BaseHandler, CallbackQueryHandler, ContextTypes, MessageHandler

from family_bot import analytics
from family_bot import clock
from family_bot import currency
from family_bot import currency_picker as picker
from family_bot import errors
from family_bot import navigation as nav
from family_bot.conversation_states import State
from family_bot.sheet_data import DATE_FMT, MONTH_FMT, Row
from family_bot.sheets import SheetsClient


QUERY_KEY = "stat_query"
ALL_CATEGORIES = "All"
MONTHS_SHOWN = 12
LAST_RECORDS_SHOWN = 3

# Button texts; the handlers below match on them.
ALL_BUTTON = "Все категории"
ONE_BUTTON = "Одна категория"
ONLY_LARGE_BUTTON = "🔶 Только крупные"
LAST_BUTTON = f"📜 Последние {LAST_RECORDS_SHOWN} записи"
PERIOD_BUTTON = "📅 Свой период"
MONTHS_BUTTON = "📆 По месяцам"
YES_BUTTON = "Да"
NO_BUTTON = "Нет"
DETAILS_BUTTON = "Показать детали"
DONE_BUTTON = "Готово"
BAD_DATE = "❌ Не понял дату. Пример: 13.07.2025"
ASK_TARGET = "💱 В какую валюту пересчитать все траты?"
PICKED_TARGET = "💱 Пересчёт в: {label}"
# Callback data prefix of the Currency picker buttons in this flow.
CURRENCY_CALLBACK = "scur:"

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
    only_large: bool = False


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
    # A text message, or the message under a tapped picker button.
    assert update.effective_message is not None
    return update.effective_message


def _user_data(context: ContextTypes.DEFAULT_TYPE) -> dict[str, Any]:
    assert context.user_data is not None
    return context.user_data


def _keyboard(rows: list[list[str]], one_time: bool = True) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [*rows, nav.home_row()], one_time_keyboard=one_time, resize_keyboard=True)


def _parse_date(text: str) -> str:
    return dparser.parse(text, dayfirst=True).strftime(DATE_FMT)


def _picker_cb(payload: str) -> str:
    return CURRENCY_CALLBACK + payload


def _inline(grid: picker.Grid) -> InlineKeyboardMarkup:
    """Picker grid plus the home button as an inline keyboard."""
    rows = [[InlineKeyboardButton(label, callback_data=data) for label, data in row]
            for row in grid]
    rows.append([nav.home_inline_button()])
    return InlineKeyboardMarkup(rows)


Source = Callable[[ContextTypes.DEFAULT_TYPE], T]
Who = Callable[[Update, ContextTypes.DEFAULT_TYPE], str]


class StatsFlow:
    """Statistics handlers with their data sources injected.

    Parameters
    ----------
    categories : Source[list[str]]
        Current category list (re-read on every call so /reloadcats applies).
    load_rows : Source[list[Row]]
        Parsed Data-sheet rows.
    sheets : Source[Optional[SheetsClient]]
        Where Recent currencies are read and saved; None without Sheets.
    who : Who
        Spender name for the current User (written next to Recent currencies).
    all_codes : Callable[[], list[str]]
        Every ISO code that can be chosen (blocking: the rate service).
    get_rate : analytics.GetRate
        FX rate source for conversion (``fx.rate`` in the bot).

    Each source takes the handler's context, so it can read the bot's Config.
    """

    def __init__(
        self,
        *,
        categories: Source[list[str]],
        load_rows: Source[list[Row]],
        sheets: Source[Optional[SheetsClient]],
        who: Who,
        all_codes: Callable[[], list[str]],
        get_rate: analytics.GetRate,
    ) -> None:
        self._categories = categories
        self._load_rows = load_rows
        self._sheets = sheets
        self._who = who
        self._all_codes = all_codes
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
            State.STAT_CONVERT_CURRENCY: [
                *text(self.on_convert_currency),
                CallbackQueryHandler(self.on_convert_button, pattern=f"^{CURRENCY_CALLBACK}"),
            ],
            State.STAT_SHOW_DETAILS: text(self.on_show_details),
        }

    async def begin(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Main-menu entry: start a fresh query and ask for the category scope."""
        _user_data(context)[QUERY_KEY] = StatQuery()
        await _message(update).reply_text(
            "📊 Статистика по всем категориям или по одной?",
            reply_markup=_keyboard([[ALL_BUTTON, ONE_BUTTON], [ONLY_LARGE_BUTTON]]),
        )
        return State.STAT_CAT

    async def this_month(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Main-menu entry: current month summary in one tap; stays on the menu."""
        month = clock.now().strftime(MONTH_FMT)
        await _message(update).reply_text(
            await asyncio.to_thread(self._month_summary, context, month))
        return State.CHOOSE_ACTION

    async def on_category(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """STAT_CAT: all categories, the category list, a chosen category or only
        Large expenses (starts a new query)."""
        text = _message(update).text or ""

        if text == ONE_BUTTON:
            await _message(update).reply_text(
                "📂 Выберите категорию:",
                reply_markup=_keyboard([[c] for c in self._categories(context)]),
            )
            return State.STAT_CAT

        if text == ONLY_LARGE_BUTTON:
            query = StatQuery(only_large=True)
        else:
            query = StatQuery(category=ALL_CATEGORIES if text == ALL_BUTTON else text)
        _user_data(context)[QUERY_KEY] = query
        await _message(update).reply_text(
            f"📊 Что показать: {_scope_label(query)}?",
            reply_markup=_keyboard([[LAST_BUTTON, PERIOD_BUTTON, MONTHS_BUTTON]]),
        )
        return State.STAT_TYPE

    async def on_type(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """STAT_TYPE: last records, custom period, or month list."""
        text = _message(update).text or ""

        if text == LAST_BUTTON:
            query = load_query(_user_data(context))
            await _message(update).reply_text(
                await asyncio.to_thread(self._last_records, context, query))
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

        await self._ask_target(update, context, ASK_TARGET)
        return State.STAT_CONVERT_CURRENCY

    async def _ask_target(self, update: Update, context: ContextTypes.DEFAULT_TYPE,
                          prompt: str) -> None:
        """Reply with the Recent currencies of this User and «🌍 Другая»."""
        assert update.effective_user is not None
        recent = await asyncio.to_thread(
            picker.load_recent, _user_data(context), self._sheets(context),
            update.effective_user.id)
        await _message(update).reply_text(
            prompt, reply_markup=_inline(picker.recent_grid(recent, _picker_cb)))

    async def on_convert_currency(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """STAT_CONVERT_CURRENCY, typed: symbol, ISO code or Russian name («дин», «GEL», «юань»)."""
        text = _message(update).text or ""
        codes = await asyncio.to_thread(self._all_codes)
        found = picker.search(text, codes)
        if len(found) == 1:
            return await self._convert(update, context, currency.stored_value(found[0]))
        if found:
            await _message(update).reply_text(
                f"{picker.SEVERAL} {picker.TYPE_HINT}",
                reply_markup=_inline(picker.choice_grid(found[:picker.PAGE_SIZE], _picker_cb)))
            return State.STAT_CONVERT_CURRENCY
        await self._ask_target(update, context, f"{picker.NOT_FOUND} {picker.TYPE_HINT}")
        return State.STAT_CONVERT_CURRENCY

    async def on_convert_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """STAT_CONVERT_CURRENCY, picker button: choose the target or turn a «🌍 Другая» page."""
        button = update.callback_query
        assert button is not None and button.data is not None
        await button.answer()
        payload = button.data[len(CURRENCY_CALLBACK):]
        code = picker.picked_code(payload)
        if code is not None:
            target = currency.stored_value(code)
            await button.edit_message_text(PICKED_TARGET.format(label=currency.label(target)))
            return await self._convert(update, context, target)
        page = picker.page_number(payload)
        if page is not None:
            codes = await asyncio.to_thread(self._all_codes)
            await button.edit_message_text(
                picker.page_text(codes, page),
                reply_markup=_inline(picker.page_grid(codes, page, _picker_cb)))
        return State.STAT_CONVERT_CURRENCY

    async def _convert(self, update: Update, context: ContextTypes.DEFAULT_TYPE,
                       target: str) -> int:
        """Remember the target, show the converted result; offer details if there are any."""
        assert update.effective_user is not None
        await asyncio.to_thread(
            picker.remember, _user_data(context), self._sheets(context),
            update.effective_user.id, self._who(update, context), target)
        query = load_query(_user_data(context))
        query.convert_to = target
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

    def _last_records(self, context: ContextTypes.DEFAULT_TYPE, query: StatQuery) -> str:
        try:
            return analytics.format_last_n(
                self._load_rows(context), n=LAST_RECORDS_SHOWN,
                category=None if query.category == ALL_CATEGORIES else query.category,
                large=query.only_large)
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
            large=query.only_large,
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
                f"📊 Статистика, {_scope_label(query)}:\n❌ Не задан период")
            return

        # Sheets read and FX requests block, so they run in a worker thread.
        stats, query.conversion_details = await asyncio.to_thread(
            self._aggregate, context, query)
        suffix = f" (в {query.convert_to})" if query.convert_to else ""
        await _message(update).reply_text(
            f"📊 Статистика {period_text}, {_scope_label(query)}{suffix}:\n{stats}")


def _scope_label(query: StatQuery) -> str:
    """
    Scope of a query as shown in prompts and result headers.

    Parameters
    ----------
    query : StatQuery
        Its Category (or ``ALL_CATEGORIES``) and the «🔶 Только крупные» choice.

    Returns
    -------
    str
        «все категории», «категория «Еда»» or «только крупные траты».
    """
    if query.only_large:
        return "только крупные траты"
    if query.category == ALL_CATEGORIES:
        return "все категории"
    return f"категория «{query.category}»"


def _details_text(query: StatQuery) -> str:
    lines = ["💱 Детали пересчёта:\n"]
    for from_cur, details in query.conversion_details.items():
        # Significant digits, so a rate into a costly Currency (дин → KWD) is not 0,0000.
        rate = f"{details['rate']:.6g}".replace(".", ",")
        lines.append(
            f"{from_cur} → {query.convert_to}:\n"
            f"  Курс: {rate}\n"
            f"  Было: {currency.format_amount(details['original_amount'], from_cur)}\n"
            f"  Стало: {currency.format_amount(details['converted_amount'], query.convert_to)}\n"
        )
    return "\n".join(lines)
