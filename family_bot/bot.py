#!/usr/bin/env python3
import os
import json
import asyncio
import logging
from dataclasses import dataclass, replace
from typing import Optional

from telegram import BotCommand, Message, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application, CommandHandler, Defaults, MessageHandler, filters,
    ConversationHandler, CallbackQueryHandler, ContextTypes,
    TypeHandler, ApplicationHandlerStop,
)

from family_bot import auth
from family_bot import clock
from family_bot import config
from family_bot import currency_picker as picker
from family_bot import errors
from family_bot import logs
from family_bot import fx
from family_bot import look
from family_bot import voice_input as vx
from family_bot import photo_input as px
from family_bot import text_input
from family_bot import draft_expense as draft
from family_bot import large_expense
from family_bot import live_card
from family_bot import flow_preset
from family_bot import monthly_summary
from family_bot import navigation as nav
from family_bot import payment_reminder as reminder
from family_bot import payment_reminder_bot as reminder_bot
from family_bot import regular
from family_bot import undo
from family_bot.flows import manual as flow_manual
from family_bot.flows import stats as flow_stats
from family_bot.conversation_states import State
from family_bot.sheet_data import parse_rows, DATE_FMT, Row
from family_bot.per_user import PerUserUpdateProcessor
from family_bot.sheets import SheetsClient
from family_bot import analytics

logger = logging.getLogger(__name__)


# ---------- Settings (Config in bot_data) ----------
def cfg(context: ContextTypes.DEFAULT_TYPE) -> config.Config:
    """Settings loaded in post_init (categories, presets, Allowlist, Sheets)."""
    return context.bot_data[config.KEY]


# Telegram command menu (the «/» button), filled in post_init.
COMMANDS = [
    BotCommand("start", "Главное меню"),
    BotCommand("dashboard", "Дашборд трат"),
    BotCommand("categories", "Список категорий"),
    BotCommand("reloadcats", "Перечитать категории и заготовки из таблицы"),
    BotCommand("cancel", "Отменить и вернуться в меню"),
    BotCommand("whoami", "Мой Telegram ID"),
    BotCommand("register", "Строка для ALLOWED_USERS"),
    BotCommand("test_connection", "Проверить связь с Google Sheets"),
]


async def post_init(app: Application) -> None:
    """Load Config and fill the command menu; log each setup problem as one line."""
    settings = await asyncio.to_thread(config.load, os.environ)
    app.bot_data[config.KEY] = settings
    for line in settings.problems:
        logger.warning("%s", line)
    if settings.sheets is not None:
        logger.info("✅ Google Sheets: %d categories, %d presets",
                    len(settings.categories), len(settings.presets))
    logger.info("✅ Allowlist: %d user(s)", len(settings.allowed_users))
    # Without Sheets the job could only fail; the Sheets problem line is logged above.
    if settings.summary_chat_id is not None and settings.sheets is not None:
        # Rows are read through bot_data at send time, so /reloadcats changes apply.
        monthly_summary.schedule(
            app.job_queue, settings.summary_chat_id, settings.summary_hour,
            lambda: load_expense_rows(app.bot_data[config.KEY].sheets))
        logger.info("✅ Monthly summary: 1st of each month, %d:00", settings.summary_hour)
        reminder_bot.schedule(
            app.job_queue, settings.summary_chat_id, settings.reminder_hour,
            lambda: load_reminder_inputs(app.bot_data[config.KEY]))
        logger.info("✅ Payment reminder: 5th and 20th, %d:00", settings.reminder_hour)
    try:
        await app.bot.set_my_commands(COMMANDS)
    except TelegramError as e:  # the menu is a convenience, the bot still works
        errors.log_exception("Command menu", e)


# ---------- Dashboard delivery ----------
# The dashboard is BUILT on a separate machine (see dashboard/run.sh): that build
# uploads the HTML to Telegram and stores the resulting file_id in the "Meta"
# worksheet. The bot itself never builds — it just re-sends that latest file_id,
# so opening the dashboard from the phone is instant and needs no server-side work.


def get_dashboard_file_id(sheets: SheetsClient) -> Optional[str]:
    """Read the latest dashboard Telegram file_id from the Meta worksheet."""
    try:
        return sheets.get_dashboard_file_id()
    except Exception as e:
        logger.warning("Error reading dashboard file_id: %s", e)
        return None


async def send_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send the latest built dashboard to the chat by its stored file_id."""
    msg = update.effective_message
    file_id = await asyncio.to_thread(get_dashboard_file_id, cfg(context).sheets)
    if not file_id:
        await msg.reply_text(
            "📊 Дашборд ещё не собран. Собери его на компьютере "
            "(dashboard/run.sh) — после сборки он появится здесь."
        )
        return
    try:
        await msg.reply_document(
            document=file_id,
            caption="Открой файл в браузере — актуальный дашборд трат. 💰",
        )
    except Exception as e:  # noqa: BLE001 - report failures to the user
        errors.log_exception("Dashboard send", e)
        await msg.reply_text("❌ Не удалось отправить дашборд. Попробуйте ещё раз")


async def dashboard_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/dashboard — send the latest built dashboard."""
    await send_dashboard(update, context)


# -------- Helpers ----------
def _telegram_display_name(user) -> str:
    """Human-readable Telegram label (not the Sheets Spender name)."""
    return user.username or user.first_name or f"User{user.id}"


def get_user_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> tuple[str, str]:
    """Gets spender name + Telegram display name for the current user."""
    user = update.effective_user
    display = _telegram_display_name(user)
    return auth.spender_name(user.id, display, cfg(context).allowed_users), display


def _is_public_command(update: Update) -> bool:
    """Commands available before allowlist (so installers can discover their ID)."""
    msg = update.effective_message
    if not msg or not msg.text:
        return False
    cmd = msg.text.split()[0].split("@", 1)[0].lower()
    return cmd in {"/whoami"}


async def ignore_edited(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """An edited Telegram message is not new input: stop it before any other handler."""
    raise ApplicationHandlerStop


EDITED_MESSAGES = MessageHandler(filters.UpdateType.EDITED, ignore_edited)


async def enforce_allowlist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Block strangers on this deploy; each family runs their own bot + sheet."""
    user = update.effective_user
    if _is_public_command(update):
        return
    settings = cfg(context)
    if user and auth.is_allowed(user.id, settings.allowed_users):
        if settings.sheets is None:
            await _reply_not_set_up(update, settings)
            raise ApplicationHandlerStop
        return

    uid = user.id if user else "?"
    text = (
        "⛔ Этот бот работает только для семьи, которая его установила.\n"
        f"Ваш Telegram ID: {look.code(str(uid))}\n\n"
        "Если это <b>ваша</b> установка, добавьте этот ID в <code>ALLOWED_USERS</code> "
        "в <code>.env</code> и перезапустите бота."
    )
    if update.callback_query:
        await update.callback_query.answer("Нет доступа", show_alert=True)
        try:
            await update.callback_query.message.reply_text(text)
        except Exception:  # noqa: BLE001 - best-effort notice
            pass
    elif update.effective_message:
        await update.effective_message.reply_text(text)
    raise ApplicationHandlerStop


async def _reply_not_set_up(update: Update, settings: config.Config) -> None:
    """Tell an allowed User that Google Sheets is not set up yet and what to fix."""
    text = look.escape("\n".join([
        "⚙️ Бот ещё не подключён к Google Sheets. Что исправить в .env:",
        *settings.problems,
        "После правки перезапустите бота. /whoami работает и сейчас.",
    ]))
    if update.callback_query:
        await update.callback_query.answer()
    if update.effective_message:
        await update.effective_message.reply_text(text)


def load_expense_rows(sheets: SheetsClient) -> list[Row]:
    """Read and parse Data-sheet rows for analytics."""
    all_values = sheets.read_data_values()
    if not all_values or len(all_values) < 2:
        return []
    return parse_rows(all_values[0], all_values[1:])


def load_reminder_inputs(settings: config.Config) -> tuple[list[dict], list[Row]]:
    """Presets and Data rows for the Payment reminder, read fresh from the sheet."""
    presets = regular.load_presets(settings.sheets, settings.default_currency)
    return presets, load_expense_rows(settings.sheets)


# Statistics conversation; data sources are read per request from Config so
# /reloadcats and test monkeypatching of load_expense_rows apply.
STATS = flow_stats.StatsFlow(
    categories=lambda context: cfg(context).categories,
    load_rows=lambda context: load_expense_rows(cfg(context).sheets),
    sheets=lambda context: cfg(context).sheets,
    who=lambda update, context: get_user_info(update, context)[0],
    all_codes=lambda: picker.CATALOG.codes(),
    get_rate=fx.rate,
)

# Manual add-expense conversation; sources read per request like STATS.
MANUAL = flow_manual.ManualFlow(
    categories=lambda context: cfg(context).categories,
    currencies=lambda context: cfg(context).currencies,
    sheets=lambda context: cfg(context).sheets,
    who=lambda update, context: get_user_info(update, context)[0],
    all_codes=lambda: picker.CATALOG.codes(),
    large_threshold=lambda context: cfg(context).large_threshold,
    threshold_currency=lambda context: cfg(context).large_threshold_currency,
    get_rate=lambda src, dst: fx.rate(src, dst),
)


# ---------- Voice / photo Draft confirmation ----------
# Extraction lives in voice_input / photo_input; review/edit/save lives in
# draft_expense. Handlers here are thin Telegram adapters. Edit steps run on
# the card as a live card (live_card): it moves to the bottom when buried, the
# person's typed answers and the voice message are deleted, a receipt photo stays.
# user_data key: AmountEdit while the User types a new amount («✏️ Сумма»).
KEY_AMOUNT_EDIT = "draft_amount_edit"


@dataclass(frozen=True)
class AmountEdit:
    """A Draft item waiting for a typed amount (or Currency), and the card to redraw."""

    draft_id: str
    index: int
    card: Message
    currency: bool = False  # waits for a Currency code or name, not an amount


async def _apply_draft_outcome(query, outcome: draft.CallbackOutcome) -> None:
    if outcome.markup_only and outcome.buttons is not None:
        await query.edit_message_reply_markup(reply_markup=nav.inline_markup(outcome.buttons))
        return
    if outcome.text is not None:
        kwargs = {}
        if outcome.buttons is not None:
            kwargs["reply_markup"] = nav.inline_markup(outcome.buttons)
        await query.edit_message_text(outcome.text, **kwargs)


async def _show_step(card: Message, session: draft.DraftSession,
                     outcome: draft.CallbackOutcome) -> Message:
    """Show an edit step of an open Draft on its live card; return the card to keep."""
    if outcome.text is None and outcome.buttons is None:
        return card
    markup = nav.inline_markup(outcome.buttons) if outcome.buttons is not None else None
    text = draft.card_text(session) if outcome.markup_only else outcome.text
    return await live_card.show(card, text or "", markup, markup_only=outcome.markup_only)


async def _show_draft(status, context: ContextTypes.DEFAULT_TYPE,
                      session: draft.DraftSession) -> None:
    """Mark Large expenses, register the Draft and turn the status message into its card."""
    context.user_data.pop(KEY_AMOUNT_EDIT, None)  # a new card ends a «✏️ Сумма» wait
    settings = cfg(context)

    def is_large(amount: float, currency: str) -> bool:
        return large_expense.suggest(
            amount, currency, threshold=settings.large_threshold,
            threshold_currency=settings.large_threshold_currency, get_rate=fx.rate)

    # Rates may be read from the network: keep the event loop free.
    await asyncio.to_thread(draft.mark_large, session, is_large)
    draft.add_draft(context.user_data, session)
    await status.edit_text(
        draft.card_text(session),
        reply_markup=nav.inline_markup(draft.main_buttons(session)),
    )


async def text_expense(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Typed text in the main menu («кофе 350») → the same Draft card as voice."""
    text = update.message.text
    if not text_input.looks_like_expense(text):
        await update.message.reply_text(text_input.HINT)
        return State.CHOOSE_ACTION
    who, _ = get_user_info(update, context)
    status = await update.message.reply_text("✍️ Разбираю…")
    today = clock.now().strftime(DATE_FMT)
    settings = cfg(context)
    try:
        session = await asyncio.to_thread(
            text_input.draft_from_text, text, categories=settings.categories,
            currencies=settings.currencies,
            today=today, who=who, default_currency=settings.default_currency)
    except Exception as e:  # noqa: BLE001
        errors.log_exception("Text extract", e)
        await status.edit_text(errors.NOT_PARSED)
        return State.CHOOSE_ACTION
    if session is None:
        await status.edit_text(text_input.HINT)
        return State.CHOOSE_ACTION
    await _show_draft(status, context, session)
    return State.CHOOSE_ACTION


async def voice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Transcribe a voice/audio message and show an editable expense draft."""
    msg = update.effective_message
    who, _ = get_user_info(update, context)
    status = await msg.reply_text("🎧 Распознаю…")

    voice = msg.voice or msg.audio
    try:
        tg_file = await context.bot.get_file(voice.file_id)
        buf = await tg_file.download_as_bytearray()
    except Exception as e:  # noqa: BLE001 - report to user
        errors.log_exception("Voice download", e)
        await status.edit_text("❌ Не смог скачать голосовое. Попробуйте ещё раз")
        return

    # STT + LLM parsing are blocking (CPU + network) — keep the event loop free.
    try:
        transcript = await asyncio.to_thread(vx.transcribe, bytes(buf))
    except Exception as e:  # noqa: BLE001
        errors.log_exception("Voice transcribe", e)
        await status.edit_text(errors.NOT_PARSED)
        return
    if not transcript:
        await status.edit_text("🤔 Не расслышал. Запиши ещё раз, чуть чётче.")
        return

    today = clock.now().strftime(DATE_FMT)
    settings = cfg(context)
    try:
        result = await asyncio.to_thread(
            vx.extract_expenses, transcript, settings.categories, settings.currencies,
            today, who, settings.default_currency)
    except Exception as e:  # noqa: BLE001
        errors.log_exception("Voice extract", e)
        await status.edit_text(f"🎧 «{look.escape(transcript)}»\n\n{errors.NOT_PARSED}")
        return

    items = result.get("expenses", [])
    if not items:
        note = result.get("note", "")
        await status.edit_text(
            f"🎧 «{look.escape(transcript)}»\n\n🤔 Не понял трату. Скажи товар, сумму и валюту, "
            "например: «чипсы сто динар сегодня»."
            + (f"\nℹ️ {look.escape(note)}" if note else ""))
        return

    session = draft.session_from_voice(
        items, who=who, transcript=transcript, note=result.get("note", ""))
    await _show_draft(status, context, session)
    await live_card.drop_reply(msg)  # the card quotes the transcript


async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Read a receipt photo/image and show an editable, category-grouped draft."""
    msg = update.effective_message
    who, _ = get_user_info(update, context)
    status = await msg.reply_text("🧾 Читаю чек…")

    # Photos come as a list of sizes (largest last); images can also arrive as a
    # document with an image mime type.
    media_type = None
    if msg.photo:
        file_id = msg.photo[-1].file_id
        media_type = "image/jpeg"  # Telegram always re-encodes photos to JPEG
    elif msg.document and (msg.document.mime_type or "").startswith("image/"):
        file_id = msg.document.file_id
        media_type = msg.document.mime_type
    else:
        await status.edit_text("🤔 Пришли фото чека картинкой.")
        return

    try:
        tg_file = await context.bot.get_file(file_id)
        buf = await tg_file.download_as_bytearray()
    except Exception as e:  # noqa: BLE001 - report to user
        errors.log_exception("Photo download", e)
        await status.edit_text("❌ Не смог скачать фото. Попробуйте ещё раз")
        return

    today = clock.now().strftime(DATE_FMT)
    settings = cfg(context)
    try:
        result = await asyncio.to_thread(
            px.extract_receipt, bytes(buf), media_type, settings.categories,
            settings.currencies, today, who, settings.default_currency)
    except Exception as e:  # noqa: BLE001
        errors.log_exception("Receipt extract", e)
        await status.edit_text(errors.NOT_PARSED)
        return

    items = result.get("expenses", [])
    if not items:
        note = result.get("note", "")
        await status.edit_text(
            "🧾 Не нашёл позиций на чеке. Сфотографируй чётче, при хорошем свете."
            + (f"\nℹ️ {look.escape(note)}" if note else ""))
        return

    session = draft.session_from_receipt(
        items,
        who=who,
        merchant=result.get("merchant", ""),
        note=result.get("note", ""),
    )
    await _show_draft(status, context, session)


async def vx_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the confirmation-card buttons (callback_data starts with 'vx:')."""
    query = update.callback_query
    # Any card button ends a pending «✏️ Сумма» wait.
    context.user_data.pop(KEY_AMOUNT_EDIT, None)
    draft_id, data = draft.parse_callback(query.data)
    session = draft.get_draft(context.user_data, draft_id)
    if session is None:
        # Keep the card text so the User sees what to send again; drop dead buttons.
        await query.answer(draft.STALE_TEXT, show_alert=True)
        await query.edit_message_reply_markup(reply_markup=None)
        return
    await query.answer()
    settings = cfg(context)
    spenders = list(dict.fromkeys(settings.allowed_users.values()))
    who, _ = get_user_info(update, context)
    user_id = update.effective_user.id

    def apply() -> draft.CallbackOutcome:
        recent = picker.load_recent(context.user_data, settings.sheets, user_id)
        result = draft.apply_callback(
            data, session, settings.categories, recent, settings.sheets,
            spenders=spenders, all_codes=lambda: picker.CATALOG.codes())
        if result.picked:
            picker.remember(context.user_data, settings.sheets, user_id, who, result.picked)
        return result

    # A save writes to Sheets, so the whole callback runs in a worker thread.
    outcome = await asyncio.to_thread(apply)
    if session.closed:
        # Saved or cancelled: the card turns into its result line where it is.
        if outcome.saved:
            buttons = undo.remember(context.user_data, list(outcome.saved), clock.now())
            outcome = draft.CallbackOutcome(text=outcome.text, buttons=buttons)
        await _apply_draft_outcome(query, outcome)
        return
    card = await _show_step(query.message, session, outcome)
    if outcome.await_amount is not None:
        context.user_data[KEY_AMOUNT_EDIT] = AmountEdit(
            draft_id=session.id, index=outcome.await_amount, card=card)
    if outcome.await_currency is not None:
        context.user_data[KEY_AMOUNT_EDIT] = AmountEdit(
            draft_id=session.id, index=outcome.await_currency, card=card,
            currency=True)


async def draft_amount_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Typed amount after «✏️ Сумма»: redraw the Draft card, keep it from the flows.

    Runs before the conversation (its own handler group). The wait ends and
    the text passes on to the conversation as usual when the Draft is gone, or
    the text is a command or has no digit (a menu button, a word): the User
    has moved on. Text with a digit that fails the amount checks is asked again
    on the card. Either way the answer is deleted (private chat).
    """
    edit = context.user_data.get(KEY_AMOUNT_EDIT)
    if not isinstance(edit, AmountEdit):
        return
    if edit.currency:
        await _draft_currency_text(update, context, edit)
        return
    text = (update.message.text or "").strip()
    session = draft.get_draft(context.user_data, edit.draft_id)
    moved_on = text.startswith("/") or not any(ch.isdigit() for ch in text)
    if session is None or session.closed or moved_on:
        context.user_data.pop(KEY_AMOUNT_EDIT, None)
        return
    await live_card.drop_reply(update.message)
    if not draft.set_amount(session, edit.index, text, cfg(context).currencies):
        step = draft.amount_step(session, edit.index, draft.AMOUNT_ERROR)
        card = await _show_step(edit.card, session, step)
        context.user_data[KEY_AMOUNT_EDIT] = replace(edit, card=card)
        raise ApplicationHandlerStop
    context.user_data.pop(KEY_AMOUNT_EDIT, None)
    shown = draft.CallbackOutcome(text=draft.card_text(session),
                                  buttons=draft.main_buttons(session))
    await _show_step(edit.card, session, shown)
    raise ApplicationHandlerStop


async def _draft_currency_text(update: Update, context: ContextTypes.DEFAULT_TYPE,
                               edit: AmountEdit) -> None:
    """Typed Currency after «🌍 Другая» in a Draft card («GEL», «юань»).

    Text with a digit, a command or a menu button means the User moved on: the
    wait ends and the text goes to the conversation. Otherwise the answer is
    deleted (private chat) and the card shows the result.
    """
    text = (update.message.text or "").strip()
    session = draft.get_draft(context.user_data, edit.draft_id)
    moved_on = (text.startswith("/") or any(ch.isdigit() for ch in text)
                or nav.is_menu_text(text))
    if session is None or session.closed or moved_on:
        context.user_data.pop(KEY_AMOUNT_EDIT, None)
        return
    await live_card.drop_reply(update.message)
    codes = await asyncio.to_thread(picker.CATALOG.codes)
    outcome = draft.search_currency(session, edit.index, text, codes)
    if outcome.picked:
        context.user_data.pop(KEY_AMOUNT_EDIT, None)
        who, _ = get_user_info(update, context)
        await asyncio.to_thread(
            picker.remember, context.user_data, cfg(context).sheets,
            update.effective_user.id, who, outcome.picked)
    card = await _show_step(edit.card, session, outcome)
    if not outcome.picked:
        context.user_data[KEY_AMOUNT_EDIT] = replace(edit, card=card)
    raise ApplicationHandlerStop


DRAFT_AMOUNT_TEXT = MessageHandler(filters.TEXT, draft_amount_text)


async def undo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """«↩️ Отменить» under «✅ Сохранено»: delete that save's rows from Data."""
    query = update.callback_query
    result = await asyncio.to_thread(
        undo.undo, context.user_data, query.data, cfg(context).sheets, clock.now())
    if not result.done:
        # Someone else's save: say why, keep the button for its owner.
        # An expired one: the button is dead for everyone, so it goes.
        await query.answer(result.text, show_alert=True)
        if result.drop_button:
            await query.edit_message_reply_markup(reply_markup=None)
        return
    await query.answer()
    await query.edit_message_text(result.text)


# ---------- Conversation steps ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    flow_stats.reset(context.user_data)
    await nav.show_main_menu(update, context)
    return State.CHOOSE_ACTION


async def choose_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    action = nav.MENU_ACTIONS.get(text)

    if action == nav.DASHBOARD:
        await send_dashboard(update, context)
        return State.CHOOSE_ACTION

    if action == nav.ADD_EXPENSE:
        return await MANUAL.begin(update, context)

    if action == nav.ADD_PRESET:
        return await flow_preset.begin_preset_picker(update, context, cfg(context).presets)

    if action == nav.SHOW_STATS:
        return await STATS.begin(update, context)

    if action == nav.THIS_MONTH:
        return await STATS.this_month(update, context)

    return await text_expense(update, context)


async def menu_mid_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """A main-menu button during manual input: drop its card, then do what the button says.

    The manual card has inline buttons only, so the menu keyboard stays on the phone.
    """
    await nav.leave_flow(context)
    return await choose_action(update, context)


MENU_MID_INPUT = MessageHandler(nav.MENU_TEXT, menu_mid_input)


# ----- Preset flow -----
async def choose_preset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await flow_preset.choose_preset(
        update,
        context,
        presets=cfg(context).presets,
        sheets=cfg(context).sheets,
        date_fmt=DATE_FMT,
        resolve_who=lambda u: get_user_info(u, context)[0],
    )


async def type_preset_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await flow_preset.type_preset_amount(
        update,
        context,
        sheets=cfg(context).sheets,
        date_fmt=DATE_FMT,
        resolve_who=lambda u: get_user_info(u, context)[0],
    )


async def show_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows current categories."""
    categories = cfg(context).categories
    if categories:
        text = f"📋 Категории ({len(categories)}):\n{', '.join(categories)}"
    else:
        text = "❌ Категории не загружены"

    await update.message.reply_text(look.escape(text))


async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows Telegram ID and allowlist status (works even if not yet allowed)."""
    user = update.effective_user
    user_id = user.id
    display = _telegram_display_name(user)

    allowed = cfg(context).allowed_users
    if auth.is_allowed(user_id, allowed):
        text = (
            f"👤 Ваш профиль:\nID: {user_id}\nИмя: {display}\n"
            f"В списке как: {allowed[user_id]}"
        )
    else:
        text = (
            f"👤 Ваш профиль:\nID: {user_id}\nИмя: {display}\n"
            f"Статус: нет в ALLOWED_USERS\n\n"
            f"Добавьте в .env и перезапустите бота:\nALLOWED_USERS={user_id}:{display}"
        )
    await update.message.reply_text(look.escape(text))


async def allowlist_hint(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/register — print an ALLOWED_USERS= line to copy into .env (does not write the file)."""
    user = update.effective_user
    user_id = user.id
    display = _telegram_display_name(user)
    name = context.args[0] if context.args else display
    existing = ",".join(f"{uid}:{n}" for uid, n in cfg(context).allowed_users.items())
    merged = f"{existing},{user_id}:{name}" if existing else f"{user_id}:{name}"
    text = (
        f"📝 Чтобы добавить члена семьи, впишите строку в .env и перезапустите бота:\n\n"
        f"ALLOWED_USERS={merged}\n\n"
        f"Ваш ID: {user_id}"
    )
    await update.message.reply_text(look.escape(text))


async def test_connection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Tests connection to Google Sheets."""
    sheets = cfg(context).sheets
    try:
        if sheets is not None and await asyncio.to_thread(sheets.test_connection):
            test_cats = await asyncio.to_thread(sheets.read_categories)
            if test_cats:
                text = f"✅ Подключение работает!\n📋 Категорий: {len(test_cats)}\n{', '.join(test_cats)}"
            else:
                text = "⚠️ Подключение работает, но в листе Config нет категорий"
        else:
            text = "❌ Нет подключения к Google Sheets"
    except Exception as e:  # noqa: BLE001 - details go to the log
        errors.log_exception("Connection test", e)
        text = "❌ Нет подключения к Google Sheets"

    await update.message.reply_text(look.escape(text))


async def reload_cats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reloads categories (and presets) from Google Sheets."""
    settings = cfg(context)
    old_cats = settings.categories.copy()
    problems = await asyncio.to_thread(config.reload, settings)
    cats = settings.categories

    if old_cats == cats:
        text = f"✅ Категории не изменились ({len(cats)}):\n{', '.join(cats)}"
    else:
        text = f"🔄 Категории обновлены ({len(cats)}):\n{', '.join(cats)}"
    if problems:
        text = "\n".join([text, "", *problems])

    await update.message.reply_text(look.escape(text))


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Clears temporary data and returns user to action selection.
    """
    await update.message.reply_text(
        "❌ Действие отменено. Начинаем заново 🙂"
    )
    return await nav.go_home(update, context)


# ---------- Main ----------
def build_application(bot_token: str) -> Application:
    """Build the Telegram Application with all handlers and the error handler."""
    # Every message is Telegram HTML: builders escape human text (look).
    # TrackingBot remembers the bot's own messages, so a buried live card moves down.
    tg_bot = live_card.TrackingBot(bot_token, defaults=Defaults(parse_mode=ParseMode.HTML))
    app = (Application.builder().bot(tg_bot).post_init(post_init)
           .concurrent_updates(PerUserUpdateProcessor()).build())

    # Run before every other handler: drop edits (group -3), then the allowlist
    # (group -2), then an amount answering a Payment reminder prompt or a typed
    # amount for a Draft card (group -1, first match wins).
    app.add_handler(EDITED_MESSAGES, group=-3)
    app.add_handler(TypeHandler(Update, enforce_allowlist), group=-2)
    app.add_handler(reminder_bot.AMOUNT_REPLY, group=-1)
    app.add_handler(DRAFT_AMOUNT_TEXT, group=-1)

    # Entry points serve a User with no conversation state: memory is wiped on
    # every deploy, so menu and home buttons must work without /start. No
    # re-entry: inside a flow its states read the text, /start and home are fallbacks.
    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            MessageHandler(nav.MENU_TEXT, choose_action),
            *nav.home_fallbacks(),
        ],
        states={
            State.CHOOSE_ACTION: [MessageHandler(nav.INPUT_TEXT, choose_action)],
            **{state: [MENU_MID_INPUT, *handlers]
               for state, handlers in MANUAL.states().items()},
            State.CHOOSE_PRESET: [MessageHandler(nav.INPUT_TEXT, choose_preset)],
            State.TYPING_PRESET_AMT: [MessageHandler(nav.INPUT_TEXT, type_preset_amount)],
            **STATS.states(),
        },
        fallbacks=[
            *nav.home_fallbacks(),
            CommandHandler("cancel", cancel),
            CommandHandler("stop", cancel),
            CommandHandler("start", start),  # Add start as fallback
        ],
        allow_reentry=False,
    )

    # Voice- and photo-driven expense entry work from any state, so register them
    # before the conversation handler (same group, earlier match wins).
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, voice_handler))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, photo_handler))
    app.add_handler(CallbackQueryHandler(vx_callback, pattern="^vx:"))
    app.add_handler(CallbackQueryHandler(undo_callback, pattern=f"^{undo.CALLBACK_PREFIX}"))
    app.add_handler(CallbackQueryHandler(reminder_bot.on_button,
                                         pattern=f"^{reminder.CALLBACK_PREFIX}"))

    app.add_handler(conv)
    app.add_handler(CommandHandler("dashboard", dashboard_cmd))
    app.add_handler(CommandHandler("reloadcats", reload_cats))
    app.add_handler(CommandHandler("categories", show_categories))
    app.add_handler(CommandHandler("test_connection", test_connection))
    app.add_handler(CommandHandler("whoami", whoami))
    app.add_handler(CommandHandler("register", allowlist_hint))
    app.add_error_handler(errors.on_error)
    return app


def main():
    # Local runs read .env here, not at import; on Render env vars come from the dashboard.
    config.load_env_file()
    logs.setup()
    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        raise SystemExit(config.problem(
            "BOT_TOKEN не задан", "получите токен у @BotFather и впишите его в .env"))

    app = build_application(bot_token)

    # --- Webhook setup ---
    # Port for Render.com
    port = int(os.environ.get('PORT', 8443))
    # URL provided by Render.com
    render_url = os.getenv("RENDER_EXTERNAL_URL")

    if not render_url:
        logger.info("RENDER_EXTERNAL_URL variable not found, running in polling mode for local development.")
        app.run_polling()
    else:
        raw_secret = os.getenv(config.WEBHOOK_SECRET_ENV)
        secret = config.webhook_secret(raw_secret)
        if not raw_secret:
            logger.warning("%s is not set, using a random secret for this run.", config.WEBHOOK_SECRET_ENV)
        webhook_url = f"{render_url}/{config.WEBHOOK_PATH}"
        logger.info("Running in webhook mode, URL: %s", webhook_url)
        # The path is public; Telegram proves itself with the secret_token header instead.
        app.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path=config.WEBHOOK_PATH,
            webhook_url=webhook_url,
            secret_token=secret,
        )


if __name__ == "__main__":
    main()
