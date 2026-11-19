#!/usr/bin/env python3
"""Manual add-expense conversation flow.

Category → amount → Currency → comment → date → save. Step values live in
``user_data`` (``cat``, ``amt``, ``cur``, ``spender``, ``comment``) until the
save; every exit goes through ``navigation.go_home``. The home button is not
checked here: ``navigation.home_fallbacks()`` handles it for every state.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Callable, Optional, TypeVar

from dateutil import parser as dparser
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update
from telegram.ext import BaseHandler, CallbackQueryHandler, ContextTypes, MessageHandler

from family_bot import amount_input
from family_bot import clock
from family_bot import currency
from family_bot import navigation as nav
from family_bot.conversation_states import State
from family_bot.expense import ExpenseRecord, append_expense
from family_bot.sheet_data import DATE_FMT
from family_bot.sheets import SheetsClient

T = TypeVar("T")
Source = Callable[[ContextTypes.DEFAULT_TYPE], T]
Who = Callable[[Update, ContextTypes.DEFAULT_TYPE], str]


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


class ManualFlow:
    """Manual add-expense handlers with their data sources injected.

    Parameters
    ----------
    categories : Source[list[str]]
        Current category list (re-read on every call so /reloadcats applies).
    currencies : Source[list[str]]
        Currency buttons offered by this install.
    sheets : Source[SheetsClient]
        Where the Expense is saved.
    who : Who
        Spender name for the current User.
    """

    def __init__(
        self,
        *,
        categories: Source[list[str]],
        currencies: Source[list[str]],
        sheets: Source[SheetsClient],
        who: Who,
    ) -> None:
        self._categories = categories
        self._currencies = currencies
        self._sheets = sheets
        self._who = who

    def states(self) -> dict[object, list[BaseHandler]]:
        """ConversationHandler states for this flow."""
        def text(callback: Callable[..., Any]) -> list[BaseHandler]:
            return [MessageHandler(nav.INPUT_TEXT, callback)]

        return {
            State.CHOOSE_CAT: text(self.choose_cat),
            State.TYPING_AMT: text(self.type_amount),
            State.CHOOSE_CUR: text(self.choose_cur),
            State.TYPING_CMNT: [
                *text(self.type_comment),
                CallbackQueryHandler(self.type_comment, pattern="^skip$"),
            ],
            State.CHOOSE_DT: [CallbackQueryHandler(self.choose_dt, pattern=nav.NOT_HOME_CALLBACK)],
            State.TYPING_DT: text(self.type_dt),
        }

    async def begin(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Main-menu entry: ask for the Category."""
        await _ask_from_list(update, "📂 Choose category:", self._categories(context))
        return State.CHOOSE_CAT

    async def choose_cat(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        text = update.message.text

        categories = self._categories(context)
        cat = _known_category(text, categories)
        if cat is None:
            await _ask_from_list(update, "❌ No such category. Choose one from the list:", categories)
            return State.CHOOSE_CAT

        context.user_data["cat"] = cat
        kb = [nav.home_row()]
        await update.message.reply_text(
            "💵 Enter amount:",
            reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
        )
        return State.TYPING_AMT

    async def type_amount(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        text = update.message.text

        parsed = amount_input.parse_amount_text(text)
        if parsed is None:
            await update.message.reply_text(
                "❌ Need an amount above zero with at most 2 decimals, e.g. 1 200 or 350 дин. Try again:")
            return State.TYPING_AMT
        amt, typed_currency = parsed
        context.user_data["amt"] = amt
        currencies = self._currencies(context)
        cur = _on_buttons(typed_currency, currencies)
        if cur is not None:
            return await self._set_currency_and_ask_comment(update, context, cur)
        await _ask_from_list(update, "💱 Currency?", currencies)
        return State.CHOOSE_CUR

    async def choose_cur(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        text = update.message.text

        currencies = self._currencies(context)
        cur = _on_buttons(currency.find(text), currencies)
        if cur is None:
            await _ask_from_list(update, "❌ No such currency. Choose one from the list:", currencies)
            return State.CHOOSE_CUR
        return await self._set_currency_and_ask_comment(update, context, cur)

    async def _set_currency_and_ask_comment(self, update: Update,
                                            context: ContextTypes.DEFAULT_TYPE,
                                            cur: str) -> int:
        context.user_data["cur"] = cur

        # Automatically detect user
        user_name = self._who(update, context)
        context.user_data["spender"] = user_name

        # Go to comment
        buttons = [
            [InlineKeyboardButton("⏭️ Skip", callback_data="skip")],
            [nav.home_inline_button()]
        ]
        await update.message.reply_text(
            f"👤 Auto-detected: {user_name}\n\n💬 Add comment or press «Skip»",
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
            [InlineKeyboardButton("📅 Today", callback_data="today"),
             InlineKeyboardButton("📆 Yesterday", callback_data="yesterday")],
            [InlineKeyboardButton("📆 Enter date", callback_data="custom")],
            [nav.home_inline_button()]
        ]
        # Use update.effective_message for reply, as it can be both Message and CallbackQuery
        await update.effective_message.reply_text(
            "📅 Expense date:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return State.CHOOSE_DT

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
            await query.edit_message_text("📅 Enter date in format DD.MM.YYYY:")
            return State.TYPING_DT

    async def type_dt(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        text = update.message.text

        try:
            date_str = dparser.parse(text, dayfirst=True).strftime(DATE_FMT)
        except Exception:
            await update.message.reply_text("❌ Cannot parse date, try 13.07.2025")
            return State.TYPING_DT
        return await self.save_row(update, context, date_str)

    async def save_row(self, update: Update, context: ContextTypes.DEFAULT_TYPE,
                       date_str: str) -> int:
        cat = context.user_data["cat"]
        amt = context.user_data["amt"]
        cur = context.user_data["cur"]
        who = context.user_data["spender"]
        cmnt = context.user_data.get("comment", "")
        append_expense(self._sheets(context), ExpenseRecord(
            date=date_str,
            category=cat,
            amount=amt,
            currency=cur,
            who=who,
            comment=cmnt,
        ))

        text = f"✅ Saved: {cat} – {amt:.2f} {cur} on {date_str}"

        # Reply to chat depending on whether it was a message or button press
        if update.callback_query:
            await update.callback_query.edit_message_text(text)
        else:
            await update.message.reply_text(text)

        return await nav.go_home(update, context)
