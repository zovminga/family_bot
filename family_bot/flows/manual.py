#!/usr/bin/env python3
"""Manual add-expense conversation flow, shown as one live card.

amount → Currency → [Large expense?] → Category → comment → date → save. Every
step edits one bot message, the card (``live_card``): filled fields on top,
the current step's question and buttons below. The person's answers are
deleted (private chat); a wrong answer shows the reason on the card and keeps
the step. After the save the card becomes the «✅ Сохранено» line with
«↩️ Отменить»; leaving the flow (``navigation.leave_flow``) removes it.

The Currency step is the shared picker (``currency_picker``): Recent currencies
of this User, «🌍 Другая» pages, or a code / Russian name typed as text. A
Currency typed with the amount («350 дин») skips the step. Above the Large
expense threshold (``large_expense.suggest``) the card asks «Это крупная
трата?». On the comment step «✅ Сегодня, без комментария» saves at once with
today's date and no comment. Step values live in ``user_data`` (``amt``,
``cur``, ``spender``, ``large``, ``cat``, ``comment``, the card under
``live_card.KEY_CARD``) until the save. The home button is not checked here:
``navigation.home_fallbacks()`` handles it for every state.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any, Callable, Mapping, Optional, TypeVar

from dateutil import parser as dparser
from telegram import Update
from telegram.ext import BaseHandler, CallbackQueryHandler, ContextTypes, MessageHandler

from family_bot import amount_input
from family_bot import clock
from family_bot import currency
from family_bot import currency_picker as picker
from family_bot import large_expense
from family_bot import live_card
from family_bot import look
from family_bot import navigation as nav
from family_bot import undo
from family_bot.conversation_states import State
from family_bot.currency import format_amount
from family_bot.draft_expense import AMOUNT_ERROR, LARGE_LINE
from family_bot.expense import ExpenseRecord, append_expense
from family_bot.sheet_data import DATE_FMT
from family_bot.sheets import SheetsClient

T = TypeVar("T")
Source = Callable[[ContextTypes.DEFAULT_TYPE], T]
Who = Callable[[Update, ContextTypes.DEFAULT_TYPE], str]
GetRate = Callable[[str, str], Optional[float]]

CARD_TITLE = "Новая трата"
TODAY_NO_COMMENT = "today_no_comment"
SKIP_COMMENT = "skip"
# Callback data prefix of the Currency picker buttons in this flow.
CURRENCY_CALLBACK = "cur:"
# Callback data prefix of the Category buttons: «mcat:<index in the list>».
CATEGORY_CALLBACK = "mcat:"
CATEGORY_COLUMNS = 2
# Large expense step: question and its two buttons.
LARGE_CALLBACK = "large:"
LARGE_YES = LARGE_CALLBACK + "yes"
LARGE_NO = LARGE_CALLBACK + "no"
LARGE_QUESTION = "🔶 Это крупная трата?"
YES_LABEL, NO_LABEL = "Да", "Нет"
# Step questions shown under the fields.
ASK_AMOUNT = "💵 Введи сумму:"
ASK_CATEGORY = "📂 Выбери категорию:"
UNKNOWN_CATEGORY = "❌ Такой категории нет. Выбери из списка:"
ASK_COMMENT = "💬 Напиши комментарий или нажми кнопку"
ASK_DATE = "📅 Дата траты:"
TYPE_DATE = "📅 Введи дату в формате ДД.ММ.ГГГГ:"
BAD_DATE = "❌ Не понял дату. Пример: 13.07.2025"
LARGE_HINT = f"Нажми «{YES_LABEL}» или «{NO_LABEL}» под вопросом «{LARGE_QUESTION}»."
COMMENT_BUTTONS: look.ButtonGrid = [
    [("✅ Сегодня, без комментария", TODAY_NO_COMMENT, look.SUCCESS)],
    [("⏭️ Пропустить", SKIP_COMMENT)],
]
DATE_BUTTONS: look.ButtonGrid = [
    [("📅 Сегодня", "today"), ("📆 Вчера", "yesterday")],
    [("📆 Другая дата", "custom")],
]
LARGE_BUTTONS: look.ButtonGrid = [[(YES_LABEL, LARGE_YES), (NO_LABEL, LARGE_NO)]]


def card_text(data: Mapping[str, Any], prompt: str, error: str = "") -> str:
    """
    The card: title, filled fields, then the error (if any) and the step's question.

    Parameters
    ----------
    data : Mapping[str, Any]
        Step values so far (``user_data``).
    prompt : str
        Plain-text question of the current step.
    error : str
        Plain-text reason the last answer was not taken.

    Returns
    -------
    str
        HTML.
    """
    lines = [f"💰 {look.bold(CARD_TITLE)}"]
    if "amt" in data:
        lines.append(look.field("Сумма", format_amount(data["amt"], data.get("cur"))))
    if "cat" in data:
        lines.append(look.field("Категория", data["cat"]))
    if data.get("comment"):
        lines.append(look.field("Комментарий", data["comment"]))
    if "spender" in data:
        lines.append(look.field("Кто", data["spender"]))
    if data.get("large"):
        lines.append(LARGE_LINE)
    lines.append("")
    if error:
        lines.append(look.escape(error))
    lines.append(look.escape(prompt))
    return "\n".join(lines)


def _known_category(text: str, categories: list[str]) -> Optional[str]:
    """Category from the list matching text (case and outer spaces ignored)."""
    key = text.strip().casefold()
    return next((c for c in categories if c.casefold() == key), None)


def _category_grid(categories: list[str]) -> look.ButtonGrid:
    buttons: list[look.Button] = [(c, f"{CATEGORY_CALLBACK}{i}") for i, c in enumerate(categories)]
    return [buttons[i:i + CATEGORY_COLUMNS] for i in range(0, len(buttons), CATEGORY_COLUMNS)]


def _on_buttons(found: Optional[currency.Currency], currencies: list[str]) -> Optional[str]:
    """Stored symbol of a registered Currency if this install offers it."""
    return found.symbol if found is not None and found.symbol in currencies else None


def _picker_cb(payload: str) -> str:
    return CURRENCY_CALLBACK + payload


async def _answered(update: Update) -> None:
    """Stop the tapped button spinning, or delete the typed answer (private chat)."""
    if update.callback_query:
        await update.callback_query.answer()
    elif update.message:
        await live_card.drop_reply(update.message)


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
        Large expense threshold.
    threshold_currency : Source[str]
        Currency (stored symbol) the threshold is in.
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
        threshold_currency: Source[str],
        get_rate: GetRate,
    ) -> None:
        self._categories = categories
        self._currencies = currencies
        self._sheets = sheets
        self._who = who
        self._all_codes = all_codes
        self._large_threshold = large_threshold
        self._threshold_currency = threshold_currency
        self._get_rate = get_rate

    def states(self) -> dict[object, list[BaseHandler]]:
        """ConversationHandler states for this flow."""
        def text(callback: Callable[..., Any]) -> list[BaseHandler]:
            return [MessageHandler(nav.INPUT_TEXT, callback)]

        return {
            State.TYPING_AMT: text(self.type_amount),
            State.CHOOSE_CUR: [
                *text(self.choose_cur),
                CallbackQueryHandler(self.on_currency_button, pattern=f"^{CURRENCY_CALLBACK}"),
            ],
            State.ASK_LARGE: [
                *text(self.on_large_text),
                CallbackQueryHandler(self.on_large_button, pattern=f"^{LARGE_CALLBACK}"),
            ],
            State.CHOOSE_CAT: [
                *text(self.choose_cat),
                CallbackQueryHandler(self.on_category_button, pattern=f"^{CATEGORY_CALLBACK}"),
            ],
            State.TYPING_CMNT: [
                *text(self.type_comment),
                CallbackQueryHandler(self.type_comment, pattern=f"^{SKIP_COMMENT}$"),
                CallbackQueryHandler(self.today_no_comment, pattern=f"^{TODAY_NO_COMMENT}$"),
            ],
            State.CHOOSE_DT: [CallbackQueryHandler(self.choose_dt, pattern=nav.NOT_HOME_CALLBACK)],
            State.TYPING_DT: text(self.type_dt),
        }

    async def _show(self, update: Update, context: ContextTypes.DEFAULT_TYPE, prompt: str,
                    grid: Optional[look.ButtonGrid] = None, error: str = "") -> None:
        """Put the current step on the card (sent first if there is none yet)."""
        data = context.user_data
        text = card_text(data, prompt, error)
        markup = nav.inline_markup(grid or [], home=True)
        query = update.callback_query
        card = query.message if query and query.message else data.get(live_card.KEY_CARD)
        if card is None:
            card = await update.effective_message.reply_text(text, reply_markup=markup)
        else:
            card = await live_card.show(card, text, markup)
        data[live_card.KEY_CARD] = card

    async def begin(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Main-menu entry: a new card asking the amount (a card left by /start goes)."""
        old = context.user_data.pop(live_card.KEY_CARD, None)
        if old is not None:
            await live_card.remove(old)
        await self._show(update, context, ASK_AMOUNT)
        return State.TYPING_AMT

    async def type_amount(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        await _answered(update)
        parsed = amount_input.parse_amount_text(update.message.text)
        if parsed is None:
            await self._show(update, context, ASK_AMOUNT, error=AMOUNT_ERROR)
            return State.TYPING_AMT
        amt, typed_currency = parsed
        context.user_data["amt"] = amt
        cur = _on_buttons(typed_currency, self._currencies(context))
        if cur is not None:
            return await self._set_currency(update, context, cur)
        await self._ask_currency(update, context, picker.ASK)
        return State.CHOOSE_CUR

    async def _ask_currency(self, update: Update, context: ContextTypes.DEFAULT_TYPE,
                            prompt: str, error: str = "") -> None:
        """The Recent currencies of this User and «🌍 Другая» on the card."""
        recent = await asyncio.to_thread(
            picker.load_recent, context.user_data, self._sheets(context),
            update.effective_user.id)
        await self._show(update, context, prompt, picker.recent_grid(recent, _picker_cb), error)

    async def choose_cur(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Typed Currency: symbol, ISO code or Russian name («дин», «GEL», «юань»)."""
        await _answered(update)
        codes = await asyncio.to_thread(self._all_codes)
        found = picker.search(update.message.text, codes)
        if len(found) == 1:
            return await self._set_currency(update, context, currency.stored_value(found[0]))
        if found:
            await self._show(update, context, f"{picker.SEVERAL} {picker.TYPE_HINT}",
                             picker.choice_grid(found[:picker.PAGE_SIZE], _picker_cb))
            return State.CHOOSE_CUR
        await self._ask_currency(update, context, picker.TYPE_HINT, error=picker.NOT_FOUND)
        return State.CHOOSE_CUR

    async def on_currency_button(self, update: Update,
                                 context: ContextTypes.DEFAULT_TYPE) -> int:
        """Picker button: choose a Currency or turn a «🌍 Другая» page."""
        await _answered(update)
        payload = update.callback_query.data[len(CURRENCY_CALLBACK):]
        code = picker.picked_code(payload)
        if code is not None:
            return await self._set_currency(update, context, currency.stored_value(code))
        page = picker.page_number(payload)
        if page is not None:
            codes = await asyncio.to_thread(self._all_codes)
            await self._show(update, context, picker.page_text(codes, page),
                             picker.page_grid(codes, page, _picker_cb))
        return State.CHOOSE_CUR

    async def _set_currency(self, update: Update, context: ContextTypes.DEFAULT_TYPE,
                            cur: str) -> int:
        """Keep the Currency; ask «Это крупная трата?» above the threshold, else the Category."""
        context.user_data["cur"] = cur
        context.user_data["spender"] = self._who(update, context)
        context.user_data["large"] = False
        if await self._above_threshold(context):
            await self._show(update, context, LARGE_QUESTION, LARGE_BUTTONS)
            return State.ASK_LARGE
        return await self._ask_category(update, context)

    async def _above_threshold(self, context: ContextTypes.DEFAULT_TYPE) -> bool:
        """Whether to ask the Large expense question for the amount and Currency."""
        return await asyncio.to_thread(
            large_expense.suggest, context.user_data["amt"], context.user_data["cur"],
            threshold=self._large_threshold(context),
            threshold_currency=self._threshold_currency(context),
            get_rate=self._get_rate)

    async def on_large_button(self, update: Update,
                              context: ContextTypes.DEFAULT_TYPE) -> int:
        """«Да» / «Нет» under «Это крупная трата?»."""
        await _answered(update)
        context.user_data["large"] = update.callback_query.data == LARGE_YES
        return await self._ask_category(update, context)

    async def on_large_text(self, update: Update,
                            context: ContextTypes.DEFAULT_TYPE) -> int:
        """Typed «да» / «нет» instead of a button; anything else repeats the hint."""
        await _answered(update)
        answer = update.message.text.strip().casefold()
        if answer not in (YES_LABEL.casefold(), NO_LABEL.casefold()):
            await self._show(update, context, LARGE_QUESTION, LARGE_BUTTONS, error=LARGE_HINT)
            return State.ASK_LARGE
        context.user_data["large"] = answer == YES_LABEL.casefold()
        return await self._ask_category(update, context)

    async def _ask_category(self, update: Update, context: ContextTypes.DEFAULT_TYPE,
                            prompt: str = ASK_CATEGORY) -> int:
        await self._show(update, context, prompt, _category_grid(self._categories(context)))
        return State.CHOOSE_CAT

    async def choose_cat(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Typed Category name (case and outer spaces ignored)."""
        await _answered(update)
        cat = _known_category(update.message.text, self._categories(context))
        if cat is None:
            return await self._ask_category(update, context, UNKNOWN_CATEGORY)
        context.user_data["cat"] = cat
        return await self._ask_comment(update, context)

    async def on_category_button(self, update: Update,
                                 context: ContextTypes.DEFAULT_TYPE) -> int:
        """Category button; a stale index (the list was reloaded) asks again."""
        await _answered(update)
        categories = self._categories(context)
        index = update.callback_query.data[len(CATEGORY_CALLBACK):]
        if not index.isdigit() or int(index) >= len(categories):
            return await self._ask_category(update, context)
        context.user_data["cat"] = categories[int(index)]
        return await self._ask_comment(update, context)

    async def _ask_comment(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        await self._show(update, context, ASK_COMMENT, COMMENT_BUTTONS)
        return State.TYPING_CMNT

    async def type_comment(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Typed comment or «⏭️ Пропустить»; then the date step."""
        await _answered(update)
        context.user_data["comment"] = "" if update.callback_query else update.message.text
        await self._show(update, context, ASK_DATE, DATE_BUTTONS)
        return State.CHOOSE_DT

    async def today_no_comment(self, update: Update,
                               context: ContextTypes.DEFAULT_TYPE) -> int:
        """Comment-step button: save with today's date and an empty comment."""
        await _answered(update)
        context.user_data["comment"] = ""
        return await self.save_row(update, context, clock.now().strftime(DATE_FMT))

    async def choose_dt(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        await _answered(update)
        data = update.callback_query.data
        if data == "today":
            return await self.save_row(update, context, clock.now().strftime(DATE_FMT))
        if data == "yesterday":
            date_str = (clock.now() - timedelta(days=1)).strftime(DATE_FMT)
            return await self.save_row(update, context, date_str)
        await self._show(update, context, TYPE_DATE)
        return State.TYPING_DT

    async def type_dt(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        await _answered(update)
        try:
            date_str = dparser.parse(update.message.text, dayfirst=True).strftime(DATE_FMT)
        except (ValueError, OverflowError):
            await self._show(update, context, TYPE_DATE, error=BAD_DATE)
            return State.TYPING_DT
        return await self.save_row(update, context, date_str)

    async def save_row(self, update: Update, context: ContextTypes.DEFAULT_TYPE,
                       date_str: str) -> int:
        """Write the Expense; the card becomes «✅ Сохранено» with «↩️ Отменить»."""
        data = context.user_data
        record = ExpenseRecord(
            date=date_str,
            category=data["cat"],
            amount=data["amt"],
            currency=data["cur"],
            who=data["spender"],
            comment=data.get("comment", ""),
            large=data.get("large", False),
        )
        await asyncio.to_thread(append_expense, self._sheets(context), record)
        await asyncio.to_thread(
            picker.remember, data, self._sheets(context),
            update.effective_user.id, record.who, record.currency)

        text = undo.saved_text([record])
        if record.large:
            text += f"\n{LARGE_LINE}"
        markup = nav.inline_markup(undo.remember(data, [record], clock.now()))
        query = update.callback_query
        card = query.message if query and query.message else data.get(live_card.KEY_CARD)
        data.pop(live_card.KEY_CARD, None)  # a result now: leave_flow keeps it
        if card is None:
            await update.effective_message.reply_text(text, reply_markup=markup)
        else:
            await live_card.show(card, text, markup)
        # Only inline buttons were sent: the main menu keyboard is still on the phone.
        await nav.leave_flow(context)
        return State.CHOOSE_ACTION
