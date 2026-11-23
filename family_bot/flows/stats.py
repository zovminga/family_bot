#!/usr/bin/env python3
"""Statistics conversation flow.

All questions go in one bot message, the questions message, which every step
edits; its buttons are inline. Typed answers (dates, a Currency name) are
deleted once read. The answer is a new message, and the questions message is
then deleted, so only the answer stays in the chat. The main-menu keyboard
stays on screen the whole time; a menu tap (or, on a step with buttons only,
any typed text) closes the questions and goes on as from the menu.

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
from dataclasses import dataclass
from typing import Any, Callable, Coroutine, Optional, TypeVar

from dateutil import parser as dparser
from dateutil.relativedelta import relativedelta
from telegram import InlineKeyboardMarkup, Message, Update
from telegram.error import BadRequest, TelegramError
from telegram.ext import BaseHandler, CallbackQueryHandler, ContextTypes, MessageHandler

from family_bot import analytics
from family_bot import clock
from family_bot import currency
from family_bot import currency_picker as picker
from family_bot import errors
from family_bot import live_card
from family_bot import look
from family_bot import navigation as nav
from family_bot.conversation_states import State
from family_bot.look import bold, escape
from family_bot.sheet_data import DATE_FMT, MONTH_FMT, Row
from family_bot.sheets import SheetsClient


QUERY_KEY = "stat_query"
# user_data key: the questions message (telegram.Message) of the open request.
CARD_KEY = "stat_card"
ALL_CATEGORIES = "All"
MONTHS_SHOWN = 12
LAST_RECORDS_SHOWN = 3

# Button labels and their callback data («st:» + payload).
CALLBACK = "st:"
ALL_BUTTON = "Все категории"
ONE_BUTTON = "Одна категория"
ONLY_LARGE_BUTTON = "🔶 Только крупные"
LAST_BUTTON = f"📜 Последние {LAST_RECORDS_SHOWN} записи"
PERIOD_BUTTON = "📅 Свой период"
MONTHS_BUTTON = "📆 По месяцам"
YES_BUTTON = "Да"
NO_BUTTON = "Нет"
ALL, ONE, LARGE = "all", "one", "large"
CATEGORY = "cat:"  # + index in the category list
LAST, PERIOD, MONTHS = "last", "period", "months"
MONTH = "month:"  # + YYYY-MM
YES, NO = "yes", "no"

ASK_SCOPE = "📊 Статистика по всем категориям или по одной?"
ASK_CATEGORY = "📂 Выбери категорию:"
ASK_MONTH = "📆 Выбери месяц:"
ASK_DATE_FROM = "📅 Напиши дату начала (ДД.ММ.ГГГГ):"
ASK_DATE_TO = "📅 Напиши дату конца (ДД.ММ.ГГГГ):"
ASK_GROUPING = "💱 Разбить по валютам?"
BAD_DATE = "❌ Не понял дату. Пример: 13.07.2025"
NO_PERIOD = "❌ Не задан период"
ASK_TARGET = "💱 В какую валюту пересчитать все траты?"
# Callback data prefix of the Currency picker buttons in this flow.
CURRENCY_CALLBACK = "scur:"

T = TypeVar("T")
Handler = Callable[[Update, Any], Coroutine[Any, Any, int]]


@dataclass
class StatQuery:
    """Parameters of one statistics request."""

    category: str = ALL_CATEGORIES
    month: Optional[str] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    convert_to: Optional[str] = None
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
    user_data.pop(CARD_KEY, None)


def _message(update: Update) -> Message:
    # A text message, or the message under a tapped button.
    assert update.effective_message is not None
    return update.effective_message


def _user_data(context: ContextTypes.DEFAULT_TYPE) -> dict[str, Any]:
    assert context.user_data is not None
    return context.user_data


def _parse_date(text: str) -> str:
    return dparser.parse(text, dayfirst=True).strftime(DATE_FMT)


def _picker_cb(payload: str) -> str:
    return CURRENCY_CALLBACK + payload


def _cb(payload: str) -> str:
    return CALLBACK + payload


def _inline(grid: look.ButtonGrid) -> InlineKeyboardMarkup:
    """Button grid plus the home button as an inline keyboard."""
    return nav.inline_markup(grid, home=True)


async def _payload(update: Update) -> str:
    """Answer the tapped button and return its data without «st:»."""
    button = update.callback_query
    assert button is not None and button.data is not None
    await button.answer()
    return button.data.removeprefix(CALLBACK)


async def _delete(message: Optional[Message]) -> None:
    """Delete a message; one that is already gone (or too old) stays as it is."""
    if message is None:
        return
    try:
        await message.delete()
    except TelegramError as e:
        errors.log_exception("Delete stats message", e)


def _month_label(month: str) -> str:
    """«октябрь 2026» for ``2026-10``."""
    title = analytics.month_title(month)
    return title[0].lower() + title[1:]


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

    def states(self, menu: Optional[Handler] = None) -> dict[object, list[BaseHandler]]:
        """
        ConversationHandler states for this flow.

        Parameters
        ----------
        menu : Optional[Handler]
            What a menu tap does once the questions are closed (the bot's
            menu handler); None stays on the menu. On a step with buttons
            only, any typed text goes there too (a typed Expense still works).
        """
        async def leave(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
            await self.close(context)
            return await menu(update, context) if menu else State.CHOOSE_ACTION

        def buttons(callback: Handler) -> list[BaseHandler]:
            return [CallbackQueryHandler(callback, pattern=f"^{CALLBACK}"),
                    MessageHandler(nav.INPUT_TEXT, leave)]

        def typed(callback: Handler) -> list[BaseHandler]:
            return [MessageHandler(nav.MENU_TEXT, leave),
                    MessageHandler(nav.INPUT_TEXT, callback)]

        home = CallbackQueryHandler(self.on_home, pattern=f"^{nav.HOME_CALLBACK}$")
        states: dict[object, list[BaseHandler]] = {
            State.STAT_CAT: buttons(self.on_category),
            State.STAT_TYPE: buttons(self.on_type),
            State.STAT_DATE_FROM: typed(self.on_date_from),
            State.STAT_DATE_TO: typed(self.on_date_to),
            State.STAT_MONTH: buttons(self.on_month),
            State.STAT_GROUP_CURRENCY: buttons(self.on_group_currency),
            State.STAT_CONVERT_CURRENCY: [
                *typed(self.on_convert_currency),
                CallbackQueryHandler(self.on_convert_button, pattern=f"^{CURRENCY_CALLBACK}"),
            ],
        }
        return {state: [home, *handlers] for state, handlers in states.items()}

    # --- the questions message ---

    async def _ask(self, update: Update, context: ContextTypes.DEFAULT_TYPE, text: str,
                   grid: Optional[look.ButtonGrid] = None) -> None:
        """Show a question in the questions message: edit it, or send it if there is none."""
        user_data = _user_data(context)
        markup = _inline(grid or [])
        card: Optional[Message] = user_data.get(CARD_KEY)
        if card is None and update.callback_query is not None:
            card = update.callback_query.message  # type: ignore[assignment]
        if card is not None:
            user_data[CARD_KEY] = card
            try:
                await card.edit_text(text, reply_markup=markup)
                return
            except BadRequest as e:
                if "not modified" in str(e).lower():
                    return
                # Deleted by the User: ask in a new message below.
        user_data[CARD_KEY] = await _message(update).reply_text(text, reply_markup=markup)

    async def _answer(self, update: Update, context: ContextTypes.DEFAULT_TYPE,
                      text: str) -> int:
        """Send the answer as a new message, delete the questions, back to the menu."""
        card: Optional[Message] = _user_data(context).get(CARD_KEY)
        # No quote: in a group Telegram would quote the questions, deleted right after.
        await (card or _message(update)).reply_text(text, do_quote=False)
        await self.close(context)
        return State.CHOOSE_ACTION

    async def close(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Delete the questions message and drop the query."""
        await _delete(_user_data(context).get(CARD_KEY))
        reset(_user_data(context))

    async def on_home(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """«🏠 В начало» under a question: the questions go away, the menu stays."""
        await _payload(update)
        await self.close(context)
        return State.CHOOSE_ACTION

    # --- steps ---

    async def begin(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Main-menu entry: start a fresh query and ask for the category scope."""
        await self.close(context)
        _user_data(context)[QUERY_KEY] = StatQuery()
        await self._ask(update, context, ASK_SCOPE, [
            [(ALL_BUTTON, _cb(ALL)), (ONE_BUTTON, _cb(ONE))],
            [(ONLY_LARGE_BUTTON, _cb(LARGE))],
        ])
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
        payload = await _payload(update)
        categories = self._categories(context)

        if payload == ONE:
            return await self._ask_category(update, context)

        if payload == LARGE:
            query = StatQuery(only_large=True)
        elif payload.startswith(CATEGORY):
            index = int(payload.removeprefix(CATEGORY))
            if not 0 <= index < len(categories):
                # The list changed (/reloadcats) under the buttons: show it again.
                return await self._ask_category(update, context)
            query = StatQuery(category=categories[index])
        else:
            query = StatQuery()
        _user_data(context)[QUERY_KEY] = query
        await self._ask(update, context, f"📊 Что показать: {_scope_label(query)}?", [
            [(LAST_BUTTON, _cb(LAST))],
            [(PERIOD_BUTTON, _cb(PERIOD)), (MONTHS_BUTTON, _cb(MONTHS))],
        ])
        return State.STAT_TYPE

    async def _ask_category(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        await self._ask(update, context, ASK_CATEGORY, [
            [(name, _cb(f"{CATEGORY}{i}"))] for i, name in enumerate(self._categories(context))])
        return State.STAT_CAT

    async def on_type(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """STAT_TYPE: last records, custom period, or month list."""
        payload = await _payload(update)

        if payload == LAST:
            query = load_query(_user_data(context))
            return await self._answer(update, context, await asyncio.to_thread(
                self._last_records, context, query))

        if payload == PERIOD:
            await self._ask(update, context, ASK_DATE_FROM)
            return State.STAT_DATE_FROM

        first_of_month = clock.now().replace(day=1)
        months = [(first_of_month - relativedelta(months=i)).strftime(MONTH_FMT)
                  for i in range(MONTHS_SHOWN)]
        await self._ask(update, context, ASK_MONTH, [
            [(analytics.month_title(m), _cb(MONTH + m))
             for m in months[i:i + 2]] for i in range(0, len(months), 2)])
        return State.STAT_MONTH

    async def on_date_from(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """STAT_DATE_FROM: parse the period start (the typed text is deleted)."""
        text = _message(update).text or ""
        await live_card.drop_reply(_message(update))
        try:
            date_from = _parse_date(text)
        except (ValueError, OverflowError):
            await self._ask(update, context, f"{BAD_DATE}\n{ASK_DATE_FROM}")
            return State.STAT_DATE_FROM
        query = load_query(_user_data(context))
        query.month, query.date_from = None, date_from
        await self._ask(update, context, ASK_DATE_TO)
        return State.STAT_DATE_TO

    async def on_date_to(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """STAT_DATE_TO: parse the period end, then ask about grouping."""
        text = _message(update).text or ""
        await live_card.drop_reply(_message(update))
        try:
            date_to = _parse_date(text)
        except (ValueError, OverflowError):
            await self._ask(update, context, f"{BAD_DATE}\n{ASK_DATE_TO}")
            return State.STAT_DATE_TO
        load_query(_user_data(context)).date_to = date_to
        return await self._ask_grouping(update, context)

    async def on_month(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """STAT_MONTH: store the month, then ask about grouping."""
        payload = await _payload(update)
        query = load_query(_user_data(context))
        query.month, query.date_from, query.date_to = payload.removeprefix(MONTH), None, None
        return await self._ask_grouping(update, context)

    async def _ask_grouping(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        await self._ask(update, context, ASK_GROUPING,
                        [[(YES_BUTTON, _cb(YES)), (NO_BUTTON, _cb(NO))]])
        return State.STAT_GROUP_CURRENCY

    async def on_group_currency(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """STAT_GROUP_CURRENCY: a block per Currency, or ask for a conversion Currency."""
        payload = await _payload(update)
        query = load_query(_user_data(context))
        if payload == YES:
            query.convert_to = None
            return await self._answer(update, context, await self._result(context, query))
        await self._ask_target(update, context, ASK_TARGET)
        return State.STAT_CONVERT_CURRENCY

    async def _ask_target(self, update: Update, context: ContextTypes.DEFAULT_TYPE,
                          prompt: str) -> None:
        """Ask with the Recent currencies of this User and «🌍 Другая»."""
        assert update.effective_user is not None
        recent = await asyncio.to_thread(
            picker.load_recent, _user_data(context), self._sheets(context),
            update.effective_user.id)
        await self._ask(update, context, prompt, picker.recent_grid(recent, _picker_cb))

    async def on_convert_currency(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """STAT_CONVERT_CURRENCY, typed: symbol, ISO code or Russian name («дин», «GEL», «юань»)."""
        text = _message(update).text or ""
        await live_card.drop_reply(_message(update))
        codes = await asyncio.to_thread(self._all_codes)
        found = picker.search(text, codes)
        if len(found) == 1:
            return await self._convert(update, context, currency.stored_value(found[0]))
        if found:
            await self._ask(update, context, f"{picker.SEVERAL} {picker.TYPE_HINT}",
                            picker.choice_grid(found[:picker.PAGE_SIZE], _picker_cb))
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
            return await self._convert(update, context, currency.stored_value(code))
        page = picker.page_number(payload)
        if page is not None:
            codes = await asyncio.to_thread(self._all_codes)
            await self._ask(update, context, picker.page_text(codes, page),
                            picker.page_grid(codes, page, _picker_cb))
        return State.STAT_CONVERT_CURRENCY

    async def _convert(self, update: Update, context: ContextTypes.DEFAULT_TYPE,
                       target: str) -> int:
        """Remember the target and answer with every amount converted."""
        assert update.effective_user is not None
        await asyncio.to_thread(
            picker.remember, _user_data(context), self._sheets(context),
            update.effective_user.id, self._who(update, context), target)
        query = load_query(_user_data(context))
        query.convert_to = target
        return await self._answer(update, context, await self._result(context, query))

    # --- answers (blocking parts run in a worker thread) ---

    def _last_records(self, context: ContextTypes.DEFAULT_TYPE, query: StatQuery) -> str:
        try:
            return analytics.format_last_n(
                self._load_rows(context), n=LAST_RECORDS_SHOWN,
                category=None if query.category == ALL_CATEGORIES else query.category,
                large=query.only_large)
        except Exception as e:  # noqa: BLE001 - details go to the log
            errors.log_exception("Last records", e)
            return "❌ Не удалось загрузить записи. Попробуй ещё раз"

    def _month_summary(self, context: ContextTypes.DEFAULT_TYPE, month: str) -> str:
        try:
            return analytics.format_month_summary(
                analytics.month_summary(self._load_rows(context), month))
        except Exception as e:  # noqa: BLE001 - details go to the log
            errors.log_exception("Month summary", e)
            return "❌ Не удалось загрузить траты. Попробуй ещё раз"

    def _period_stats(self, context: ContextTypes.DEFAULT_TYPE, query: StatQuery) -> str:
        """Load rows and build the answer (blocking: Sheets + FX)."""
        return analytics.format_period_stats(
            self._load_rows(context),
            _result_header(query),
            category=query.category,
            month=query.month,
            date_from=query.date_from,
            date_to=query.date_to,
            large=query.only_large,
            convert_to=query.convert_to,
            get_rate=self._get_rate,
        )

    async def _result(self, context: ContextTypes.DEFAULT_TYPE, query: StatQuery) -> str:
        """The answer text for a month or a period."""
        if not (query.month or (query.date_from and query.date_to)):
            return f"{_result_header(query)}\n\n{NO_PERIOD}"
        return await asyncio.to_thread(self._period_stats, context, query)


def _scope_label(query: StatQuery) -> str:
    """
    Scope of a query as shown in questions.

    Parameters
    ----------
    query : StatQuery
        Its Category (or ``ALL_CATEGORIES``) and the «🔶 Только крупные» choice.

    Returns
    -------
    str
        «все категории», «категория «Еда»» or «только крупные траты» (HTML).
    """
    if query.only_large:
        return "только крупные траты"
    if query.category == ALL_CATEGORIES:
        return "все категории"
    return escape(f"категория «{query.category}»")


def _result_header(query: StatQuery) -> str:
    """
    First line of an answer: «📊 <b>Траты за октябрь 2026 · Еда · в дин</b>».

    Only Large expenses get «🔶» and «Крупные траты».
    """
    mark, what = ("🔶", "Крупные траты") if query.only_large else ("📊", "Траты")
    if query.month:
        period = f"за {_month_label(query.month)}"
    elif query.date_from and query.date_to:
        period = f"с {query.date_from} по {query.date_to}"
    else:
        period = ""
    parts = [f"{what} {period}".strip()]
    if query.category != ALL_CATEGORIES and not query.only_large:
        parts.append(query.category)
    if query.convert_to:
        parts.append(f"в {query.convert_to}")
    return f"{mark} {bold(' · '.join(parts))}"
