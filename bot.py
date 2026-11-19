#!/usr/bin/env python3
import os
import json
import asyncio
from datetime import timedelta
from dateutil import parser as dparser
from typing import Optional

from telegram import (
    Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters,
    ConversationHandler, CallbackQueryHandler, ContextTypes,
    TypeHandler, ApplicationHandlerStop,
)

import amount_input
import auth
import clock
import config
import currency
import errors
import voice_input as vx
import photo_input as px
import draft_expense as draft
import flow_preset
import navigation as nav
from flows import stats as flow_stats
from conversation_states import (
    CHOOSE_ACTION, CHOOSE_CAT, TYPING_AMT, CHOOSE_CUR, TYPING_CMNT, CHOOSE_DT, TYPING_DT,
    CHOOSE_PRESET,
)
from expense import ExpenseRecord, append_expense
from sheet_data import normalize_rows, DATE_FMT
from sheets import SheetsClient
import analytics


# ---------- Settings (Config in bot_data) ----------
def cfg(context: ContextTypes.DEFAULT_TYPE) -> config.Config:
    """Settings loaded in post_init (categories, presets, Allowlist, Sheets)."""
    return context.bot_data[config.KEY]


async def post_init(app: Application) -> None:
    """Load Config once the bot starts; print each setup problem as one line."""
    settings = config.load(os.environ)
    app.bot_data[config.KEY] = settings
    for line in settings.problems:
        print(line)
    if settings.sheets is not None:
        print(f"✅ Google Sheets: {len(settings.categories)} categories, "
              f"{len(settings.presets)} presets")
    print(f"✅ Allowlist: {len(settings.allowed_users)} user(s)")


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
        print(f"Error reading dashboard file_id: {e}")
        return None


async def send_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send the latest built dashboard to the chat by its stored file_id."""
    msg = update.effective_message
    file_id = get_dashboard_file_id(cfg(context).sheets)
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


async def _reply_not_set_up(update: Update, settings: config.Config) -> None:
    """Tell an allowed User that Google Sheets is not set up yet and what to fix."""
    text = "\n".join([
        "⚙️ Бот ещё не подключён к Google Sheets. Что исправить в .env:",
        *settings.problems,
        "После правки перезапустите бота. /whoami работает и сейчас.",
    ])
    if update.callback_query:
        await update.callback_query.answer()
    if update.effective_message:
        await update.effective_message.reply_text(text)


def load_expense_rows(sheets: SheetsClient) -> list[dict]:
    """Read and normalize Data-sheet rows for analytics."""
    all_values = sheets.read_data_values()
    if not all_values or len(all_values) < 2:
        return []
    return normalize_rows(all_values[0], all_values[1:])


# Statistics conversation; data sources are read per request from Config so
# /reloadcats and test monkeypatching of load_expense_rows apply.
STATS = flow_stats.StatsFlow(
    categories=lambda context: cfg(context).categories,
    load_rows=lambda context: load_expense_rows(cfg(context).sheets),
    fallback_currencies=lambda context: cfg(context).currencies,
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


async def _apply_draft_outcome(query, outcome: draft.CallbackOutcome) -> None:
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
            today, who, DEFAULT_CURRENCY)
    except Exception as e:  # noqa: BLE001
        errors.log_exception("Voice extract", e)
        await status.edit_text(f"🎧 «{transcript}»\n\n{errors.NOT_PARSED}")
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
    draft.add_draft(context.user_data, session)
    await status.edit_text(
        draft.card_text(session),
        reply_markup=_inline_markup(draft.main_buttons(session)),
    )


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
            settings.currencies, today, who, DEFAULT_CURRENCY)
    except Exception as e:  # noqa: BLE001
        errors.log_exception("Receipt extract", e)
        await status.edit_text(errors.NOT_PARSED)
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
    draft.add_draft(context.user_data, session)
    await status.edit_text(
        draft.card_text(session),
        reply_markup=_inline_markup(draft.main_buttons(session)),
    )


async def vx_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the confirmation-card buttons (callback_data starts with 'vx:')."""
    query = update.callback_query
    await query.answer()
    draft_id, data = draft.parse_callback(query.data)
    session = draft.get_draft(context.user_data, draft_id)
    if session is None:
        await query.edit_message_text("Черновик уже не активен.")
        return
    settings = cfg(context)
    outcome = draft.apply_callback(
        data, session, settings.categories, settings.currencies, settings.sheets)
    await _apply_draft_outcome(query, outcome)


# ---------- Conversation steps ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    flow_stats.reset(context.user_data)
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
        await _ask_from_list(update, "📂 Choose category:", cfg(context).categories)
        return CHOOSE_CAT

    if text in (
        "🧾 Add preset",
        "Add preset",
        "🧾 Add default expense",  # alias — old button label
        "Add default expense",
    ):
        return await flow_preset.begin_preset_picker(update, context, cfg(context).presets)

    if text == "📊 Show statistics" or text == "Show statistics":
        return await STATS.begin(update, context)

    await update.message.reply_text("Please press a button 😉")
    return CHOOSE_ACTION


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


# ----- Manual add-expense flow -----
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


async def choose_cat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if nav.is_home_text(text):
        return await nav.go_home(update, context)

    categories = cfg(context).categories
    cat = _known_category(text, categories)
    if cat is None:
        await _ask_from_list(update, "❌ No such category. Choose one from the list:", categories)
        return CHOOSE_CAT

    context.user_data["cat"] = cat
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
    
    parsed = amount_input.parse_amount_text(text)
    if parsed is None:
        await update.message.reply_text(
            "❌ Need an amount above zero with at most 2 decimals, e.g. 1 200 or 350 дин. Try again:")
        return TYPING_AMT
    amt, typed_currency = parsed
    context.user_data["amt"] = amt
    currencies = cfg(context).currencies
    cur = _on_buttons(typed_currency, currencies)
    if cur is not None:
        return await _set_currency_and_ask_comment(update, context, cur)
    await _ask_from_list(update, "💱 Currency?", currencies)
    return CHOOSE_CUR


async def choose_cur(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    
    # Handle "To start" button
    if nav.is_home_text(text):
        return await nav.go_home(update, context)
    
    currencies = cfg(context).currencies
    cur = _on_buttons(currency.find(text), currencies)
    if cur is None:
        await _ask_from_list(update, "❌ No such currency. Choose one from the list:", currencies)
        return CHOOSE_CUR
    return await _set_currency_and_ask_comment(update, context, cur)


async def _set_currency_and_ask_comment(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                        cur: str) -> int:
    context.user_data["cur"] = cur

    # Automatically detect user
    user_name, username = get_user_info(update, context)
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
        date_str = clock.now().strftime(DATE_FMT)
        return await save_row(update, context, date_str)
    elif query.data == "yesterday":
        date_str = (clock.now() - timedelta(days=1)).strftime(DATE_FMT)
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
    append_expense(cfg(context).sheets, ExpenseRecord(
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


async def show_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows current categories."""
    categories = cfg(context).categories
    if categories:
        text = f"📋 Current categories ({len(categories)}):\n{', '.join(categories)}"
    else:
        text = "❌ Categories not loaded"
    
    await update.message.reply_text(text)


async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows Telegram ID and allowlist status (works even if not yet allowed)."""
    user = update.effective_user
    user_id = user.id
    display = _telegram_display_name(user)

    allowed = cfg(context).allowed_users
    if auth.is_allowed(user_id, allowed):
        text = (
            f"👤 Your profile:\nID: {user_id}\nName: {display}\n"
            f"Allowed as: {allowed[user_id]}"
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
    existing = ",".join(f"{uid}:{n}" for uid, n in cfg(context).allowed_users.items())
    merged = f"{existing},{user_id}:{name}" if existing else f"{user_id}:{name}"
    text = (
        f"📝 Add this household member via .env (then restart the bot):\n\n"
        f"ALLOWED_USERS={merged}\n\n"
        f"Your ID: {user_id}"
    )
    await update.message.reply_text(text)


async def test_connection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Tests connection to Google Sheets."""
    sheets = cfg(context).sheets
    try:
        if sheets is not None and sheets.test_connection():
            test_cats = sheets.read_categories()
            if test_cats:
                text = f"✅ Connection successful!\n📋 Available categories: {len(test_cats)}\n{', '.join(test_cats)}"
            else:
                text = "⚠️  Connection works, but categories not found in Config sheet"
        else:
            text = "❌ Google Sheets connection error"
    except Exception as e:  # noqa: BLE001 - details go to the log
        errors.log_exception("Connection test", e)
        text = "❌ Google Sheets connection error"

    await update.message.reply_text(text)


async def reload_cats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reloads categories (and presets) from Google Sheets."""
    settings = cfg(context)
    old_cats = settings.categories.copy()
    problems = config.reload(settings)
    cats = settings.categories

    if old_cats == cats:
        text = f"✅ Categories already up to date ({len(cats)}):\n{', '.join(cats)}"
    else:
        text = f"🔄 Categories updated ({len(cats)}):\n{', '.join(cats)}"
    if problems:
        text = "\n".join([text, "", *problems])

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
def build_application(bot_token: str) -> Application:
    """Build the Telegram Application with all handlers and the error handler."""
    app = Application.builder().token(bot_token).post_init(post_init).build()

    # Run before every other handler: drop edits (group -2), then the allowlist (group -1).
    app.add_handler(EDITED_MESSAGES, group=-2)
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
            **STATS.states(),
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
    app.add_error_handler(errors.on_error)
    return app


def main():
    # Local runs read .env here, not at import; on Render env vars come from the dashboard.
    config.load_env_file()
    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        raise SystemExit(config.problem(
            "BOT_TOKEN is not set", "get a token from @BotFather and put it in .env"))

    app = build_application(bot_token)

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
