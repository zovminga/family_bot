#!/usr/bin/env python3
import os
import json
import asyncio
from datetime import datetime, timedelta
from dateutil import parser as dparser
from dateutil.relativedelta import relativedelta
from typing import Optional

from dotenv import load_dotenv
from telegram import (
    Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters,
    ConversationHandler, CallbackQueryHandler, ContextTypes,
    TypeHandler, ApplicationHandlerStop,
)

import auth
import currency
import voice_input as vx
import photo_input as px
import regular
import draft_expense as draft
import flow_preset
import navigation as nav
from conversation_states import (
    CHOOSE_ACTION, CHOOSE_CAT, TYPING_AMT, CHOOSE_CUR, TYPING_CMNT, CHOOSE_DT, TYPING_DT,
    CHOOSE_PRESET,
    STAT_CAT, STAT_TYPE, STAT_DATE_FROM, STAT_DATE_TO, STAT_MONTH,
    STAT_GROUP_CURRENCY, STAT_CONVERT_CURRENCY, STAT_SHOW_DETAILS,
)
from expense import ExpenseRecord, append_expense
from sheet_data import normalize_rows, DATE_FMT, MONTH_FMT
from sheets import SheetsClient, open_client
import analytics

# Load family_bot/.env for local runs (on Render env vars come from the dashboard).
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))


# ---------- Google Sheets (via SheetsClient) ----------
# Process-wide client; set in main() and also used at import for categories/presets.
sheets: Optional[SheetsClient] = None


def get_sheets() -> SheetsClient:
    """Return the process SheetsClient, opening one from env if needed."""
    global sheets
    if sheets is None:
        sheets = open_client()
    return sheets


def test_google_sheets_connection() -> bool:
    """Tests connection to Google Sheets."""
    try:
        ok = get_sheets().test_connection()
        if ok:
            print("✅ Google Sheets connection successful")
        return ok
    except Exception as e:
        print(f"❌ Google Sheets connection error: {e}")
        return False


def validate_categories(categories: list[str]) -> list[str]:
    """Validates and cleans category list."""
    if not categories:
        return []
    
    # Remove duplicates, preserving order
    seen = set()
    unique_categories = []
    for cat in categories:
        cat_clean = cat.strip()
        if cat_clean and cat_clean not in seen:
            seen.add(cat_clean)
            unique_categories.append(cat_clean)
    
    return unique_categories


def load_categories() -> list[str]:
    """Reads category list from column A of Config sheet."""
    try:
        categories = validate_categories(get_sheets().read_categories())
        
        # Check that categories are not empty
        if not categories:
            print("⚠️  Warning: Config sheet is empty or contains no categories")
            return []
            
        print(f"✅ Loaded {len(categories)} categories: {', '.join(categories)}")
        return categories
        
    except Exception as e:
        print(f"❌ Error loading categories: {e}")
        # Return default categories in case of error
        return ["Food", "Transport", "Entertainment", "Other"]


# ---------- Bot constants ----------
def initialize_categories():
    """Initializes categories on bot startup."""
    global CATS
    
    # Test Google Sheets connection
    if not test_google_sheets_connection():
        print("⚠️  Using default categories due to connection issues")
        CATS = ["Food", "Transport", "Entertainment", "Other"]
        return CATS
    
    CATS = load_categories()
    if not CATS:
        print("⚠️  Using default categories")
        CATS = ["Food", "Transport", "Entertainment", "Other"]
    return CATS

CATS = initialize_categories()


def load_defaults() -> list[dict]:
    """Load all preset expenses (monthly + one-off) from Regular_expenses."""
    try:
        items = regular.load_defaults(get_sheets())
        print(f"✅ Loaded {len(items)} presets")
        return items
    except Exception as e:  # noqa: BLE001 - degrade to no presets
        print(f"⚠️  Could not load presets: {e}")
        return []


DEFAULTS = load_defaults()
CURS = currency.symbols()

# Allowlist: Telegram ID -> spender name on the Data sheet.
# Loaded from ALLOWED_USERS in .env (see .env.example). Empty = nobody.
ALLOWED_USERS: dict[int, str] = auth.load_allowed_users()


# ---------- Dashboard delivery ----------
# The dashboard is BUILT on a separate machine (see dashboard/run.sh): that build
# uploads the HTML to Telegram and stores the resulting file_id in the "Meta"
# worksheet. The bot itself never builds — it just re-sends that latest file_id,
# so opening the dashboard from the phone is instant and needs no server-side work.


def get_dashboard_file_id() -> Optional[str]:
    """Read the latest dashboard Telegram file_id from the Meta worksheet."""
    try:
        return get_sheets().get_dashboard_file_id()
    except Exception as e:
        print(f"Error reading dashboard file_id: {e}")
        return None


async def send_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send the latest built dashboard to the chat by its stored file_id."""
    msg = update.effective_message
    file_id = get_dashboard_file_id()
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
        await msg.reply_text(f"❌ Не удалось отправить дашборд: {e}")


async def dashboard_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/dashboard — send the latest built dashboard."""
    await send_dashboard(update, context)


# -------- Helpers ----------
def _telegram_display_name(user) -> str:
    """Human-readable Telegram label (not the Sheets Spender name)."""
    return user.username or user.first_name or f"User{user.id}"


def get_user_info(update: Update) -> tuple[str, str]:
    """Gets spender name + Telegram display name for the current user."""
    user = update.effective_user
    display = _telegram_display_name(user)
    return auth.spender_name(user.id, display, ALLOWED_USERS), display


def _is_public_command(update: Update) -> bool:
    """Commands available before allowlist (so installers can discover their ID)."""
    msg = update.effective_message
    if not msg or not msg.text:
        return False
    cmd = msg.text.split()[0].split("@", 1)[0].lower()
    return cmd in {"/whoami"}


async def enforce_allowlist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Block strangers on this deploy; each family runs their own bot + sheet."""
    user = update.effective_user
    if user and auth.is_allowed(user.id, ALLOWED_USERS):
        return
    if _is_public_command(update):
        return

    uid = user.id if user else "?"
    text = (
        "⛔ This bot is private to the family that installed it.\n"
        f"Your Telegram ID: `{uid}`\n\n"
        "If this is *your* install, add that ID to `ALLOWED_USERS` in `.env` "
        "and restart the bot."
    )
    if update.callback_query:
        await update.callback_query.answer("Access denied", show_alert=True)
        try:
            await update.callback_query.message.reply_text(text, parse_mode="Markdown")
        except Exception:  # noqa: BLE001 - best-effort notice
            pass
    elif update.effective_message:
        await update.effective_message.reply_text(text, parse_mode="Markdown")
    raise ApplicationHandlerStop


def load_expense_rows() -> list[dict]:
    """Read and normalize Data-sheet rows for analytics."""
    all_values = get_sheets().read_data_values()
    if not all_values or len(all_values) < 2:
        return []
    return normalize_rows(all_values[0], all_values[1:])


def get_currencies_from_sheet() -> list[str]:
    """Gets list of unique currencies from Google Sheets."""
    try:
        return analytics.unique_currencies(load_expense_rows(), fallback=CURS)
    except Exception as e:
        print(f"Error getting currencies: {e}")
        return CURS


def get_last_n_records(n: int = 3, category: str = None) -> str:
    """Returns last N records from Google Sheets, optionally filtered by category."""
    try:
        return analytics.format_last_n(load_expense_rows(), n=n, category=category)
    except Exception as e:
        return f"❌ Error getting records: {e}"


def compute_stats(cat, month=None, date_from=None, date_to=None,
                 group_by_currency=True, convert_to_currency=None):
    """Computes statistics with support for custom periods and currency conversion.
    Returns tuple: (stats_text, conversion_details_dict)"""
    return analytics.aggregate(
        load_expense_rows(),
        category=cat,
        month=month,
        date_from=date_from,
        date_to=date_to,
        group_by_currency=group_by_currency,
        convert_to_currency=convert_to_currency,
        get_rate=analytics.http_exchange_rate,
    )


# ---------- Voice / photo Draft confirmation ----------
# Extraction lives in voice_input / photo_input; review/edit/save lives in
# draft_expense. Handlers here are thin Telegram adapters.
DEFAULT_CURRENCY = "дин"  # used when the currency wasn't spoken


def _inline_markup(rows: draft.ButtonGrid) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data=cb) for label, cb in row]
        for row in rows
    ])


async def _apply_draft_outcome(query, user_data: dict, outcome: draft.CallbackOutcome) -> None:
    if outcome.clear_session:
        draft.DraftSession.clear(user_data)
    if outcome.markup_only and outcome.buttons is not None:
        await query.edit_message_reply_markup(reply_markup=_inline_markup(outcome.buttons))
        return
    if outcome.text is not None:
        kwargs = {}
        if outcome.buttons is not None:
            kwargs["reply_markup"] = _inline_markup(outcome.buttons)
        await query.edit_message_text(outcome.text, **kwargs)


async def voice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Transcribe a voice/audio message and show an editable expense draft."""
    msg = update.effective_message
    who, _ = get_user_info(update)
    status = await msg.reply_text("🎧 Распознаю…")

    voice = msg.voice or msg.audio
    try:
        tg_file = await context.bot.get_file(voice.file_id)
        buf = await tg_file.download_as_bytearray()
    except Exception as e:  # noqa: BLE001 - report to user
        await status.edit_text(f"❌ Не смог скачать голосовое: {e}")
        return

    # STT + LLM parsing are blocking (CPU + network) — keep the event loop free.
    try:
        transcript = await asyncio.to_thread(vx.transcribe, bytes(buf))
    except Exception as e:  # noqa: BLE001
        await status.edit_text(f"❌ Ошибка распознавания: {e}")
        return
    if not transcript:
        await status.edit_text("🤔 Не расслышал. Запиши ещё раз, чуть чётче.")
        return

    today = datetime.now().strftime(DATE_FMT)
    try:
        result = await asyncio.to_thread(
            vx.extract_expenses, transcript, CATS, CURS, today, who, DEFAULT_CURRENCY)
    except Exception as e:  # noqa: BLE001
        await status.edit_text(f"🎧 «{transcript}»\n\n❌ Не смог разобрать: {e}")
        return

    items = result.get("expenses", [])
    if not items:
        note = result.get("note", "")
        await status.edit_text(
            f"🎧 «{transcript}»\n\n🤔 Не понял трату. Скажи товар, сумму и валюту, "
            "например: «чипсы сто динар сегодня»." + (f"\nℹ️ {note}" if note else ""))
        return

    session = draft.session_from_voice(
        items, who=who, transcript=transcript, note=result.get("note", ""))
    session.store(context.user_data)
    await status.edit_text(
        draft.card_text(session),
        reply_markup=_inline_markup(draft.main_buttons(session)),
    )


async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Read a receipt photo/image and show an editable, category-grouped draft."""
    msg = update.effective_message
    who, _ = get_user_info(update)
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
        await status.edit_text(f"❌ Не смог скачать фото: {e}")
        return

    today = datetime.now().strftime(DATE_FMT)
    try:
        result = await asyncio.to_thread(
            px.extract_receipt, bytes(buf), media_type, CATS, CURS,
            today, who, DEFAULT_CURRENCY)
    except Exception as e:  # noqa: BLE001
        await status.edit_text(f"❌ Не смог распознать чек: {e}")
        return

    items = result.get("expenses", [])
    if not items:
        note = result.get("note", "")
        await status.edit_text(
            "🧾 Не нашёл позиций на чеке. Сфотографируй чётче, при хорошем свете."
            + (f"\nℹ️ {note}" if note else ""))
        return

    session = draft.session_from_receipt(
        items,
        who=who,
        merchant=result.get("merchant", ""),
        note=result.get("note", ""),
    )
    session.store(context.user_data)
    await status.edit_text(
        draft.card_text(session),
        reply_markup=_inline_markup(draft.main_buttons(session)),
    )


async def vx_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the confirmation-card buttons (callback_data starts with 'vx:')."""
    query = update.callback_query
    await query.answer()
    data = query.data[len(draft.CALLBACK_PREFIX):]
    session = draft.DraftSession.load(context.user_data)
    outcome = draft.apply_callback(
        data, session, CATS, CURS, get_sheets())
    if not outcome.clear_session:
        session.store(context.user_data)
    await _apply_draft_outcome(query, context.user_data, outcome)


# ---------- Conversation steps ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await nav.show_main_menu(update, context)
    return CHOOSE_ACTION


async def choose_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if nav.is_home_text(text):
        return await nav.go_home(update, context)

    if text in ("📈 Dashboard", "Dashboard"):
        await send_dashboard(update, context)
        return CHOOSE_ACTION

    if text == "💰 Add expense" or text == "Add expense":
        kb = [[c] for c in CATS]
        kb.append(nav.home_row())
        await update.message.reply_text(
            "📂 Choose category:",
            reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True)
        )
        return CHOOSE_CAT

    if text in (
        "🧾 Add preset",
        "Add preset",
        "🧾 Add default expense",  # alias — old button label
        "Add default expense",
    ):
        return await flow_preset.begin_preset_picker(update, context, DEFAULTS)

    if text == "📊 Show statistics" or text == "Show statistics":
        kb = [["All categories", "Specific category"]]
        kb.append(nav.home_row())
        await update.message.reply_text(
            "📊 Statistics for all categories or specific?",
            reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True)
        )
        return STAT_CAT

    await update.message.reply_text("Please press a button 😉")
    return CHOOSE_ACTION


# ----- Preset flow -----
async def choose_preset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await flow_preset.choose_preset(
        update,
        context,
        defaults=DEFAULTS,
        sheets=get_sheets(),
        date_fmt=DATE_FMT,
        resolve_who=lambda u: get_user_info(u)[0],
    )


# ----- Manual add-expense flow -----
async def choose_cat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if nav.is_home_text(text):
        return await nav.go_home(update, context)

    context.user_data["cat"] = text
    kb = [nav.home_row()]
    await update.message.reply_text(
        "💵 Enter amount:",
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
    )
    return TYPING_AMT


async def type_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    
    # Handle "To start" button
    if nav.is_home_text(text):
        return await nav.go_home(update, context)
    
    try:
        amt = float(text.replace(",", "."))
    except ValueError:
        await update.message.reply_text("❌ Need a number. Try again:")
        return TYPING_AMT
    context.user_data["amt"] = amt
    kb = [[c] for c in CURS]
    kb.append(nav.home_row())
    await update.message.reply_text(
        "💱 Currency?",
        reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True)
    )
    return CHOOSE_CUR


async def choose_cur(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    
    # Handle "To start" button
    if nav.is_home_text(text):
        return await nav.go_home(update, context)
    
    context.user_data["cur"] = text
    
    # Automatically detect user
    user_name, username = get_user_info(update)
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
    return TYPING_CMNT


async def type_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # This handler is called for both text messages and "skip" button press
    if update.callback_query:
        await update.callback_query.answer()
        if nav.is_home_callback(update.callback_query.data):
            return await nav.go_home(update, context)
        context.user_data["comment"] = ""
    else:
        text = update.message.text
        if nav.is_home_text(text):
            return await nav.go_home(update, context)
        context.user_data["comment"] = text

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
    return CHOOSE_DT


async def choose_dt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if nav.is_home_callback(query.data):
        return await nav.go_home(update, context)

    if query.data == "today":
        date_str = datetime.now().strftime(DATE_FMT)
        return await save_row(update, context, date_str)
    elif query.data == "yesterday":
        date_str = (datetime.now() - timedelta(days=1)).strftime(DATE_FMT)
        return await save_row(update, context, date_str)
    else:
        await query.edit_message_text("📅 Enter date in format DD.MM.YYYY:")
        return TYPING_DT


async def type_dt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if nav.is_home_text(text):
        return await nav.go_home(update, context)

    try:
        date_str = dparser.parse(text, dayfirst=True).strftime(DATE_FMT)
    except Exception:
        await update.message.reply_text("❌ Cannot parse date, try 13.07.2025")
        return TYPING_DT
    return await save_row(update, context, date_str)


async def save_row(update: Update, context: ContextTypes.DEFAULT_TYPE, date_str: str):
    cat = context.user_data["cat"]
    amt = context.user_data["amt"]
    cur = context.user_data["cur"]
    who = context.user_data["spender"]
    cmnt = context.user_data.get("comment", "")
    append_expense(get_sheets(), ExpenseRecord(
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


# ----- Stats flow -----
async def stat_cat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles category selection for statistics."""
    text = update.message.text
    
    # Handle "To start" button
    if nav.is_home_text(text):
        return await nav.go_home(update, context)
    
    if text == "All categories":
        context.user_data["stat_cat"] = "All"
    elif text == "Specific category":
        # Show list of categories
        kb = [[c] for c in CATS]
        kb.append(nav.home_row())
        await update.message.reply_text(
            "📂 Choose category:",
            reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True)
        )
        return STAT_CAT
    else:
        # Specific category selected
        context.user_data["stat_cat"] = text
    
    # Now ask for statistics type
    cat = context.user_data["stat_cat"]
    kb = [["📜 Last 3 records", "📅 Custom period", "📆 By months"]]
    kb.append(nav.home_row())
    await update.message.reply_text(
        f"📊 Statistics type for category '{cat}':",
        reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True)
    )
    return STAT_TYPE


async def stat_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles statistics type selection."""
    text = update.message.text
    
    # Handle "To start" button
    if nav.is_home_text(text):
        return await nav.go_home(update, context)
    
    if text == "📜 Last 3 records":
        # Show last 3 records immediately
        cat = context.user_data.get("stat_cat", "All")
        last_records = get_last_n_records(3, cat if cat != "All" else None)
        await update.message.reply_text(last_records)
        return await nav.go_home(update, context)
    
    elif text == "📅 Custom period":
        # Ask for start date
        kb = [nav.home_row()]
        await update.message.reply_text(
            "📅 Enter start date (DD.MM.YYYY):",
            reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
        )
        return STAT_DATE_FROM
    
    elif text == "📆 By months":
        # Show list of months
        now = datetime.now()
        months = [(now.replace(day=1) - relativedelta(months=i)).strftime(MONTH_FMT) for i in range(12)]
        kb = [[m] for m in months]
        kb.append(nav.home_row())
        await update.message.reply_text(
            "📆 Choose month:",
            reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True)
        )
        return STAT_MONTH
    
    else:
        await update.message.reply_text("Please choose a valid option")
        return STAT_TYPE


async def stat_date_from(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles start date input for custom period."""
    text = update.message.text
    
    if nav.is_home_text(text):
        return await nav.go_home(update, context)
    
    try:
        date_from = dparser.parse(text, dayfirst=True).strftime(DATE_FMT)
        context.user_data["stat_date_from"] = date_from
        kb = [nav.home_row()]
        await update.message.reply_text(
            "📅 Enter end date (DD.MM.YYYY):",
            reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
        )
        return STAT_DATE_TO
    except Exception:
        await update.message.reply_text("❌ Cannot parse date, try 13.07.2025")
        return STAT_DATE_FROM


async def stat_date_to(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles end date input for custom period."""
    text = update.message.text
    
    if nav.is_home_text(text):
        return await nav.go_home(update, context)
    
    try:
        date_to = dparser.parse(text, dayfirst=True).strftime(DATE_FMT)
        context.user_data["stat_date_to"] = date_to
        
        # Ask about currency grouping
        kb = [["Yes", "No"]]
        kb.append(nav.home_row())
        await update.message.reply_text(
            "💱 Group by currencies?",
            reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True)
        )
        return STAT_GROUP_CURRENCY
    except Exception:
        await update.message.reply_text("❌ Cannot parse date, try 13.07.2025")
        return STAT_DATE_TO


async def stat_month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles month selection for statistics."""
    text = update.message.text
    
    if nav.is_home_text(text):
        return await nav.go_home(update, context)
    
    context.user_data["stat_month"] = text
    
    # Ask about currency grouping
    kb = [["Yes", "No"]]
    kb.append(nav.home_row())
    await update.message.reply_text(
        "💱 Group by currencies?",
        reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True)
    )
    return STAT_GROUP_CURRENCY


async def stat_group_currency(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles currency grouping choice."""
    text = update.message.text
    
    if nav.is_home_text(text):
        return await nav.go_home(update, context)
    
    group_by_currency = text == "Yes"
    context.user_data["stat_group_currency"] = group_by_currency
    
    if group_by_currency:
        # Show statistics with grouping
        await show_statistics_result(update, context)
        return CHOOSE_ACTION
    else:
        # Ask for currency to convert to
        currencies = get_currencies_from_sheet()
        kb = [[c] for c in currencies]
        kb.append(nav.home_row())
        await update.message.reply_text(
            "💱 Convert all expenses to which currency?",
            reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True)
        )
        return STAT_CONVERT_CURRENCY


async def stat_convert_currency(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles currency selection for conversion."""
    text = update.message.text
    
    if nav.is_home_text(text):
        return await nav.go_home(update, context)
    
    context.user_data["stat_convert_to"] = text
    
    # Show statistics and ask about details
    await show_statistics_result(update, context, ask_details=True)
    return STAT_SHOW_DETAILS


async def stat_show_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles showing conversion details or finishing."""
    text = update.message.text

    if nav.is_home_text(text):
        return await nav.go_home(update, context)

    if text == "Show details":
        conversion_details = context.user_data.get("conversion_details", {})
        if conversion_details:
            lines = ["💱 Conversion details:\n"]
            for from_cur, details in conversion_details.items():
                lines.append(
                    f"{from_cur} → {context.user_data.get('stat_convert_to', '?')}:\n"
                    f"  Rate: {details['rate']:.4f}\n"
                    f"  Original: {details['original_amount']:,.2f} {from_cur}\n"
                    f"  Converted: {details['converted_amount']:,.2f} "
                    f"{context.user_data.get('stat_convert_to', '?')}\n"
                )
            await update.message.reply_text("\n".join(lines))
        else:
            await update.message.reply_text("No conversion details available")

    return await nav.go_home(update, context)


async def show_statistics_result(update: Update, context: ContextTypes.DEFAULT_TYPE, ask_details: bool = False):
    """Shows statistics result and optionally asks for details."""
    cat = context.user_data.get("stat_cat", "All")
    group_by_currency = context.user_data.get("stat_group_currency", True)
    convert_to = context.user_data.get("stat_convert_to")

    month = context.user_data.get("stat_month")
    date_from = context.user_data.get("stat_date_from")
    date_to = context.user_data.get("stat_date_to")

    if month:
        stats, conversion_details = compute_stats(
            cat, month=month, group_by_currency=group_by_currency,
            convert_to_currency=convert_to
        )
        period_text = f"for {month}"
    elif date_from and date_to:
        stats, conversion_details = compute_stats(
            cat, date_from=date_from, date_to=date_to,
            group_by_currency=group_by_currency, convert_to_currency=convert_to
        )
        period_text = f"from {date_from} to {date_to}"
    else:
        stats = "❌ Error: period not specified"
        conversion_details = {}
        period_text = ""

    if conversion_details:
        context.user_data["conversion_details"] = conversion_details

    if convert_to:
        stats_text = f"📊 Statistics {period_text}, category '{cat}' (in {convert_to}):\n{stats}"
    else:
        stats_text = f"📊 Statistics {period_text}, category '{cat}':\n{stats}"

    await update.message.reply_text(stats_text)

    if ask_details and conversion_details:
        kb = [["Show details", "Done (thanks)"]]
        kb.append(nav.home_row())
        await update.message.reply_text(
            "Would you like to see conversion details?",
            reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True)
        )
    else:
        await nav.show_main_menu(update, context)


async def show_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows current categories."""
    if CATS:
        text = f"📋 Current categories ({len(CATS)}):\n{', '.join(CATS)}"
    else:
        text = "❌ Categories not loaded"
    
    await update.message.reply_text(text)


async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows Telegram ID and allowlist status (works even if not yet allowed)."""
    user = update.effective_user
    user_id = user.id
    display = _telegram_display_name(user)

    if auth.is_allowed(user_id, ALLOWED_USERS):
        text = (
            f"👤 Your profile:\nID: {user_id}\nName: {display}\n"
            f"Allowed as: {ALLOWED_USERS[user_id]}"
        )
    else:
        text = (
            f"👤 Your profile:\nID: {user_id}\nName: {display}\n"
            f"Status: not in ALLOWED_USERS\n\n"
            f"Add to .env and restart:\nALLOWED_USERS={user_id}:{display}"
        )
    await update.message.reply_text(text)


async def allowlist_hint(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/register — print an ALLOWED_USERS= line to copy into .env (does not write the file)."""
    user = update.effective_user
    user_id = user.id
    display = _telegram_display_name(user)
    name = context.args[0] if context.args else display
    existing = ",".join(f"{uid}:{n}" for uid, n in ALLOWED_USERS.items())
    merged = f"{existing},{user_id}:{name}" if existing else f"{user_id}:{name}"
    text = (
        f"📝 Add this household member via .env (then restart the bot):\n\n"
        f"ALLOWED_USERS={merged}\n\n"
        f"Your ID: {user_id}"
    )
    await update.message.reply_text(text)


async def test_connection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Tests connection to Google Sheets."""
    try:
        # Test connection
        if test_google_sheets_connection():
            # Try to load categories
            test_cats = load_categories()
            if test_cats:
                text = f"✅ Connection successful!\n📋 Available categories: {len(test_cats)}\n{', '.join(test_cats)}"
            else:
                text = "⚠️  Connection works, but categories not found in Config sheet"
        else:
            text = "❌ Google Sheets connection error"
    except Exception as e:
        text = f"❌ Error: {str(e)}"
    
    await update.message.reply_text(text)


async def reload_cats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reloads categories (and presets) from Google Sheets."""
    global CATS, DEFAULTS
    old_cats = CATS.copy()
    CATS = load_categories()
    DEFAULTS = load_defaults()
    
    if CATS:
        if old_cats == CATS:
            text = f"✅ Categories already up to date ({len(CATS)}):\n{', '.join(CATS)}"
        else:
            text = f"🔄 Categories updated ({len(CATS)}):\n{', '.join(CATS)}"
    else:
        text = "❌ Failed to load categories. Check Config sheet in Google Sheets."
    
    await update.message.reply_text(text)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Clears temporary data and returns user to action selection.
    """
    await update.message.reply_text(
        "❌ Action cancelled. Starting over 🙂"
    )
    return await nav.go_home(update, context)


# ---------- Main ----------
def main():
    # Get token from environment variables
    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        raise RuntimeError("Environment variable BOT_TOKEN not found.")

    global ALLOWED_USERS
    ALLOWED_USERS = auth.load_allowed_users()
    if not ALLOWED_USERS:
        print(
            "⚠️  ALLOWED_USERS is empty — nobody can use the bot except /whoami. "
            "Set ALLOWED_USERS in .env (see .env.example)."
        )
    else:
        print(f"✅ Allowlist: {len(ALLOWED_USERS)} user(s)")

    # Initialize Google Sheets connection
    global sheets
    sheets = open_client()

    app = Application.builder().token(bot_token).build()

    # Run before every other handler (group -1).
    app.add_handler(TypeHandler(Update, enforce_allowlist), group=-1)

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            CHOOSE_ACTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, choose_action)],
            CHOOSE_CAT: [MessageHandler(filters.TEXT & ~filters.COMMAND, choose_cat)],
            TYPING_AMT: [MessageHandler(filters.TEXT & ~filters.COMMAND, type_amount)],
            CHOOSE_CUR: [MessageHandler(filters.TEXT & ~filters.COMMAND, choose_cur)],
            TYPING_CMNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, type_comment),
                CallbackQueryHandler(type_comment, pattern="^(skip|to_start)$")
            ],
            CHOOSE_DT: [CallbackQueryHandler(choose_dt)],
            TYPING_DT: [MessageHandler(filters.TEXT & ~filters.COMMAND, type_dt)],
            CHOOSE_PRESET: [MessageHandler(filters.TEXT & ~filters.COMMAND, choose_preset)],
            STAT_CAT: [MessageHandler(filters.TEXT & ~filters.COMMAND, stat_cat)],
            STAT_TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, stat_type)],
            STAT_DATE_FROM: [MessageHandler(filters.TEXT & ~filters.COMMAND, stat_date_from)],
            STAT_DATE_TO: [MessageHandler(filters.TEXT & ~filters.COMMAND, stat_date_to)],
            STAT_MONTH: [MessageHandler(filters.TEXT & ~filters.COMMAND, stat_month)],
            STAT_GROUP_CURRENCY: [MessageHandler(filters.TEXT & ~filters.COMMAND, stat_group_currency)],
            STAT_CONVERT_CURRENCY: [MessageHandler(filters.TEXT & ~filters.COMMAND, stat_convert_currency)],
            STAT_SHOW_DETAILS: [MessageHandler(filters.TEXT & ~filters.COMMAND, stat_show_details)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CommandHandler("stop", cancel),
            CommandHandler("start", start),  # Add start as fallback
        ],
        allow_reentry=True,
    )

    # Voice- and photo-driven expense entry work from any state, so register them
    # before the conversation handler (same group, earlier match wins).
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, voice_handler))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, photo_handler))
    app.add_handler(CallbackQueryHandler(vx_callback, pattern="^vx:"))

    app.add_handler(conv)
    app.add_handler(CommandHandler("dashboard", dashboard_cmd))
    app.add_handler(CommandHandler("reloadcats", reload_cats))
    app.add_handler(CommandHandler("categories", show_categories))
    app.add_handler(CommandHandler("test_connection", test_connection))
    app.add_handler(CommandHandler("whoami", whoami))
    app.add_handler(CommandHandler("register", allowlist_hint))

    # --- Webhook setup ---
    # Port for Render.com
    port = int(os.environ.get('PORT', 8443))
    # URL provided by Render.com
    render_url = os.getenv("RENDER_EXTERNAL_URL")

    if not render_url:
        print("RENDER_EXTERNAL_URL variable not found, running in polling mode for local development.")
        app.run_polling()
    else:
        print(f"Running in webhook mode, URL: {render_url}")
        # Start webhook. Token is used as secret path in URL.
        app.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path=bot_token,
            webhook_url=f"{render_url}/{bot_token}"
        )


if __name__ == "__main__":
    main()
