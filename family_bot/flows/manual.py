#!/usr/bin/env python3
"""Manual add-expense conversation flow.

Category → amount → Currency → [Large expense?] → comment → date → save. The
Currency step is the shared picker (``currency_picker``): Recent currencies of
this User, «🌍 Другая» pages, or a code / Russian name typed as text. A
Currency typed with the amount («350 дин») skips the step. Above the Large
expense threshold (``large_expense.suggest``) the bot asks «Это крупная
трата?» before the comment step, so no comment-step button skips it. On the
comment step «✅ Сегодня, без комментария» saves at once with today's date
and no comment. Step values live in ``user_data`` (``cat``, ``amt``, ``cur``,
``spender``, ``large``, ``comment``) until the save; every exit goes through ``navigation.go_home``.
The home button is not checked here: ``navigation.home_fallbacks()`` handles
it for every state.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any, Callable, Optional, TypeVar

from dateutil import parser as dparser
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update
from telegram.ext import BaseHandler, CallbackQueryHandler, ContextTypes, MessageHandler

from family_bot import amount_input
from family_bot import clock
from family_bot import currency
from family_bot import currency_picker as picker
from family_bot import large_expense
from family_bot.currency import format_amount
from family_bot import navigation as nav
from family_bot import undo
from family_bot.conversation_states import State
from family_bot.expense import ExpenseRecord, append_expense
from family_bot.sheet_data import DATE_FMT
from family_bot.sheets import SheetsClient

T = TypeVar("T")
Source = Callable[[ContextTypes.DEFAULT_TYPE], T]
Who = Callable[[Update, ContextTypes.DEFAULT_TYPE], str]
GetRate = Callable[[str, str], Optional[float]]

TODAY_NO_COMMENT = "today_no_comment"
# Callback data prefix of the Currency picker buttons in this flow.
CURRENCY_CALLBACK = "cur:"
# Large expense step: question and its two buttons.
LARGE_CALLBACK = "large:"
LARGE_YES = LARGE_CALLBACK + "yes"
LARGE_NO = LARGE_CALLBACK + "no"
LARGE_QUESTION = "🔶 Это крупная трата?"
YES_LABEL, NO_LABEL = "Да", "Нет"


async def _ask_from_list(update: Update, prompt: str, options: list[str]) -> None:
    """Reply with prompt and one button per option, plus the home button."""
    kb = [[c] for c in options]
    kb.append(nav.home_row())
    await update.message.reply_text(
        prompt,
        reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True)
    )


def _known_category(text: str, categories: list[str]) -> Optional[str]:
    """Category from the list matching text (case and outer spaces ignored)."""
    key = text.strip().casefold()
    return next((c for c in categories if c.casefold() == key), None)


def _on_buttons(found: Optional[currency.Currency], currencies: list[str]) -> Optional[str]:
    """Stored symbol of a registered Currency if this install offers it."""
    return found.symbol if found is not None and found.symbol in currencies else None


def _picker_cb(payload: str) -> str:
    return CURRENCY_CALLBACK + payload


def _inline(grid: picker.Grid) -> InlineKeyboardMarkup:
    """Picker grid plus the home button as an inline keyboard."""
    rows = [[InlineKeyboardButton(label, callback_data=data) for label, data in row]
            for row in grid]
    rows.append([nav.home_inline_button()])
    return InlineKeyboardMarkup(rows)


class ManualFlow:
    """Manual add-expense handlers with their data sources injected.

    Parameters
    ----------
    categories : Source[list[str]]
        Current category list (re-read on every call so /reloadcats applies).
    currencies : Source[list[str]]
        Registered Currencies a typed amount («350 дин») may carry.
    sheets : Source[SheetsClient]
        Where the Expense and Recent currencies are saved.
    who : Who
        Spender name for the current User.
    all_codes : Callable[[], list[str]]
        Every ISO code that can be chosen (blocking: the rate service).
    large_threshold : Source[float]
        Large expense threshold in the base Currency.
    base_currency : Source[str]
        Base Currency (stored symbol) the threshold is in.
    get_rate : GetRate
        Exchange rate (blocking: the rate service), ``fx.rate``.
    """

    def __init__(
        self,
        *,
        categories: Source[list[str]],
        currencies: Source[list[str]],
        sheets: Source[SheetsClient],
        who: Who,
        all_codes: Callable[[], list[str]],
        large_threshold: Source[float],
        base_currency: Source[str],
        get_rate: GetRate,
    ) -> None:
        self._categories = categories
        self._currencies = currencies
        self._sheets = sheets
        self._who = who
        self._all_codes = all_codes
        self._large_threshold = large_threshold
        self._base_currency = base_currency
        self._get_rate = get_rate

    def states(self) -> dict[object, list[BaseHandler]]:
        """ConversationHandler states for this flow."""
        def text(callback: Callable[..., Any]) -> list[BaseHandler]:
            return [MessageHandler(nav.INPUT_TEXT, callback)]

        return {
            State.CHOOSE_CAT: text(self.choose_cat),
            State.TYPING_AMT: text(self.type_amount),
            State.CHOOSE_CUR: [
                *text(self.choose_cur),
                CallbackQueryHandler(self.on_currency_button, pattern=f"^{CURRENCY_CALLBACK}"),
            ],
            State.ASK_LARGE: [
                *text(self.on_large_text),
                CallbackQueryHandler(self.on_large_button, pattern=f"^{LARGE_CALLBACK}"),
            ],
            State.TYPING_CMNT: [
                *text(self.type_comment),
                CallbackQueryHandler(self.type_comment, pattern="^skip$"),
                CallbackQueryHandler(self.today_no_comment, pattern=f"^{TODAY_NO_COMMENT}$"),
            ],
            State.CHOOSE_DT: [CallbackQueryHandler(self.choose_dt, pattern=nav.NOT_HOME_CALLBACK)],
            State.TYPING_DT: text(self.type_dt),
        }

    async def begin(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Main-menu entry: ask for the Category."""
        await _ask_from_list(update, "📂 Выберите категорию:", self._categories(context))
        return State.CHOOSE_CAT

    async def choose_cat(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        text = update.message.text

        categories = self._categories(context)
        cat = _known_category(text, categories)
        if cat is None:
            await _ask_from_list(update, "❌ Такой категории нет. Выберите из списка:", categories)
            return State.CHOOSE_CAT

        context.user_data["cat"] = cat
        kb = [nav.home_row()]
        await update.message.reply_text(
            "💵 Введите сумму:",
            reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
        )
        return State.TYPING_AMT

    async def type_amount(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        text = update.message.text

        parsed = amount_input.parse_amount_text(text)
        if parsed is None:
            await update.message.reply_text(amount_input.RETRY_TEXT)
            return State.TYPING_AMT
        amt, typed_currency = parsed
        context.user_data["amt"] = amt
        currencies = self._currencies(context)
        cur = _on_buttons(typed_currency, currencies)
        if cur is not None:
            return await self._set_currency_and_ask_comment(update, context, cur)
        await self._ask_currency(update, context, picker.ASK)
        return State.CHOOSE_CUR

    async def _ask_currency(self, update: Update, context: ContextTypes.DEFAULT_TYPE,
                            prompt: str) -> None:
        """Reply with the Recent currencies of this User and «🌍 Другая»."""
        recent = await asyncio.to_thread(
            picker.load_recent, context.user_data, self._sheets(context),
            update.effective_user.id)
        await update.effective_message.reply_text(
            prompt, reply_markup=_inline(picker.recent_grid(recent, _picker_cb)))

    async def choose_cur(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Typed Currency: symbol, ISO code or Russian name («дин», «GEL», «юань»)."""
        text = update.message.text
        codes = await asyncio.to_thread(self._all_codes)
        found = picker.search(text, codes)
        if len(found) == 1:
            return await self._set_currency_and_ask_comment(
                update, context, currency.stored_value(found[0]))
        if found:
            await update.message.reply_text(
                f"{picker.SEVERAL} {picker.TYPE_HINT}",
                reply_markup=_inline(picker.choice_grid(found[:picker.PAGE_SIZE], _picker_cb)))
            return State.CHOOSE_CUR
        await self._ask_currency(update, context, f"{picker.NOT_FOUND} {picker.TYPE_HINT}")
        return State.CHOOSE_CUR

    async def on_currency_button(self, update: Update,
                                 context: ContextTypes.DEFAULT_TYPE) -> int:
        """Picker button: choose a Currency or turn a «🌍 Другая» page."""
        query = update.callback_query
        await query.answer()
        payload = query.data[len(CURRENCY_CALLBACK):]
        code = picker.picked_code(payload)
        if code is not None:
            cur = currency.stored_value(code)
            await query.edit_message_text(f"💱 Валюта: {currency.label(cur)}")
            return await self._set_currency_and_ask_comment(update, context, cur)
        page = picker.page_number(payload)
        if page is not None:
            codes = await asyncio.to_thread(self._all_codes)
            await query.edit_message_text(
                picker.page_text(codes, page),
                reply_markup=_inline(picker.page_grid(codes, page, _picker_cb)))
        return State.CHOOSE_CUR

    async def _set_currency_and_ask_comment(self, update: Update,
                                            context: ContextTypes.DEFAULT_TYPE,
                                            cur: str) -> int:
        context.user_data["cur"] = cur

        # Automatically detect user
        context.user_data["spender"] = self._who(update, context)
        context.user_data["large"] = False

        if await self._above_threshold(context):
            await update.effective_message.reply_text(
                LARGE_QUESTION,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(YES_LABEL, callback_data=LARGE_YES),
                     InlineKeyboardButton(NO_LABEL, callback_data=LARGE_NO)],
                    [nav.home_inline_button()],
                ]))
            return State.ASK_LARGE
        return await self._ask_comment(update, context)

    async def _above_threshold(self, context: ContextTypes.DEFAULT_TYPE) -> bool:
        """Whether to ask the Large expense question for the amount and Currency."""
        return await asyncio.to_thread(
            large_expense.suggest, context.user_data["amt"], context.user_data["cur"],
            threshold=self._large_threshold(context), base=self._base_currency(context),
            get_rate=self._get_rate)

    async def on_large_button(self, update: Update,
                              context: ContextTypes.DEFAULT_TYPE) -> int:
        """«Да» / «Нет» under «Это крупная трата?»."""
        query = update.callback_query
        await query.answer()
        large = query.data == LARGE_YES
        context.user_data["large"] = large
        await query.edit_message_text(
            f"{LARGE_QUESTION} {YES_LABEL if large else NO_LABEL}")
        return await self._ask_comment(update, context)

    async def on_large_text(self, update: Update,
                            context: ContextTypes.DEFAULT_TYPE) -> int:
        """Typed «да» / «нет» instead of a button; anything else repeats the hint."""
        answer = update.message.text.strip().casefold()
        if answer not in (YES_LABEL.casefold(), NO_LABEL.casefold()):
            await update.message.reply_text(
                f"Нажмите «{YES_LABEL}» или «{NO_LABEL}» под вопросом «{LARGE_QUESTION}».")
            return State.ASK_LARGE
        context.user_data["large"] = answer == YES_LABEL.casefold()
        return await self._ask_comment(update, context)

    async def _ask_comment(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        user_name = context.user_data["spender"]
        buttons = [
            [InlineKeyboardButton("✅ Сегодня, без комментария", callback_data=TODAY_NO_COMMENT)],
            [InlineKeyboardButton("⏭️ Пропустить", callback_data="skip")],
            [nav.home_inline_button()]
        ]
        await update.effective_message.reply_text(
            f"👤 Кто: {user_name}\n\n💬 Напишите комментарий или нажмите кнопку",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return State.TYPING_CMNT

    async def type_comment(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        # This handler is called for both text messages and "skip" button press
        if update.callback_query:
            await update.callback_query.answer()
            context.user_data["comment"] = ""
        else:
            context.user_data["comment"] = update.message.text

        # Choose date
        buttons = [
            [InlineKeyboardButton("📅 Сегодня", callback_data="today"),
             InlineKeyboardButton("📆 Вчера", callback_data="yesterday")],
            [InlineKeyboardButton("📆 Другая дата", callback_data="custom")],
            [nav.home_inline_button()]
        ]
        # Use update.effective_message for reply, as it can be both Message and CallbackQuery
        await update.effective_message.reply_text(
            "📅 Дата траты:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return State.CHOOSE_DT

    async def today_no_comment(self, update: Update,
                               context: ContextTypes.DEFAULT_TYPE) -> int:
        """Comment-step button: save with today's date and an empty comment."""
        await update.callback_query.answer()
        context.user_data["comment"] = ""
        return await self.save_row(update, context, clock.now().strftime(DATE_FMT))

    async def choose_dt(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        await query.answer()

        if query.data == "today":
            date_str = clock.now().strftime(DATE_FMT)
            return await self.save_row(update, context, date_str)
        elif query.data == "yesterday":
            date_str = (clock.now() - timedelta(days=1)).strftime(DATE_FMT)
            return await self.save_row(update, context, date_str)
        else:
            await query.edit_message_text("📅 Введите дату в формате ДД.ММ.ГГГГ:")
            return State.TYPING_DT

    async def type_dt(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        text = update.message.text

        try:
            date_str = dparser.parse(text, dayfirst=True).strftime(DATE_FMT)
        except Exception:
            await update.message.reply_text("❌ Не понял дату. Пример: 13.07.2025")
            return State.TYPING_DT
        return await self.save_row(update, context, date_str)

    async def save_row(self, update: Update, context: ContextTypes.DEFAULT_TYPE,
                       date_str: str) -> int:
        cat = context.user_data["cat"]
        amt = context.user_data["amt"]
        cur = context.user_data["cur"]
        who = context.user_data["spender"]
        cmnt = context.user_data.get("comment", "")
        large = context.user_data.get("large", False)
        record = ExpenseRecord(
            date=date_str,
            category=cat,
            amount=amt,
            currency=cur,
            who=who,
            comment=cmnt,
            large=large,
        )
        await asyncio.to_thread(append_expense, self._sheets(context), record)
        await asyncio.to_thread(
            picker.remember, context.user_data, self._sheets(context),
            update.effective_user.id, who, cur)

        text = f"✅ Сохранено: {cat} — {format_amount(amt, cur)}, {date_str}"
        if large:
            text += "\n🔶 Крупная трата"
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton(label, callback_data=data) for label, data in row]
            for row in undo.remember(context.user_data, [record], clock.now())
        ])

        # Reply to chat depending on whether it was a message or button press
        if update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=markup)
        else:
            await update.message.reply_text(text, reply_markup=markup)

        return await nav.go_home(update, context)
