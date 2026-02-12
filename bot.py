#!/usr/bin/env python3
import os
import json
from datetime import datetime, timedelta
from dateutil import parser as dparser
from typing import Optional

import pandas as pd
import requests
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from telegram import (
    Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters,
    ConversationHandler, CallbackQueryHandler, ContextTypes
)


# ---------- Google Sheets ----------
def test_google_sheets_connection():
    """Tests connection to Google Sheets."""
    try:
        open_sheet("Config")
        print("✅ Google Sheets connection successful")
        return True
    except Exception as e:
        print(f"❌ Google Sheets connection error: {e}")
        return False


def open_sheet(sheet_name="Data"):
    scope = ["https://www.googleapis.com/auth/drive",
             "https://www.googleapis.com/auth/spreadsheets"]

    # Get file path from environment variable
    creds_path = os.getenv("GOOGLE_CREDS_PATH")
    if not creds_path:
        raise RuntimeError("Environment variable GOOGLE_CREDS_PATH not found.")

    # Authorize using file at specified path
    creds = ServiceAccountCredentials.from_json_keyfile_name(creds_path, scope)

    gc = gspread.authorize(creds)
    sheet_name_env = os.getenv("SHEET_NAME")
    if not sheet_name_env:
        raise RuntimeError("Environment variable SHEET_NAME not found.")

    sh = gc.open(sheet_name_env)
    return sh.worksheet(sheet_name)


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
        cfg_ws = open_sheet("Config")  # ← sheet name where list is stored
        col = cfg_ws.col_values(1)  # A:A
        col = [c.strip() for c in col if c.strip()]  # remove empty
        categories = col[1:] if len(col) > 1 else []  # skip header
        
        # Validate categories
        categories = validate_categories(categories)
        
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
CURS = ["₽", "дин", "€", "¥"]
MONTH_FMT = "%Y-%m"
DATE_FMT = "%d.%m.%Y"
SPENDERS = ["Lisa", "Azat"]

# Global variable for data sheet
sheet = None

# Dictionary to map Telegram ID to user names
# Replace with real Telegram user IDs
TELEGRAM_USERS = {
    # Example: 123456789: "Lisa",
    248826020: "Azat",
}
(
    CHOOSE_ACTION, CHOOSE_CAT, TYPING_AMT, CHOOSE_CUR,
    TYPING_CMNT,
    CHOOSE_DT, TYPING_DT,
    STAT_CAT, STAT_TYPE, STAT_DATE_FROM, STAT_DATE_TO, STAT_MONTH,
    STAT_GROUP_CURRENCY, STAT_CONVERT_CURRENCY, STAT_SHOW_DETAILS
) = range(15)


# -------- Helpers ----------
def month_of(date_str: str) -> str:
    return datetime.strptime(date_str, DATE_FMT).strftime(MONTH_FMT)


def get_user_info(update: Update) -> tuple[str, str]:
    """Gets user information from Telegram."""
    user = update.effective_user
    user_id = user.id
    username = user.username or user.first_name or f"User{user_id}"
    
    # Check if user is in dictionary
    if user_id in TELEGRAM_USERS:
        return TELEGRAM_USERS[user_id], username
    else:
        # If user not found, return their name from Telegram
        return username, username


def sheet_append(row):
    sheet.append_row(row, value_input_option="USER_ENTERED")


def get_exchange_rate(from_currency: str, to_currency: str) -> Optional[float]:
    """Gets exchange rate via exchangerate-api.com API."""
    try:
        if from_currency == to_currency:
            return 1.0
        
        # Currency mapping for API
        currency_map = {
            "₽": "RUB",
            "дин": "RSD", 
            "€": "EUR",
            "¥": "JPY",
            "$": "USD"
        }
        
        from_cur = currency_map.get(from_currency, from_currency)
        to_cur = currency_map.get(to_currency, to_currency)
        
        # Use exchangerate-api.com (free)
        url = f"https://api.exchangerate-api.com/v4/latest/{from_cur}"
        response = requests.get(url, timeout=5)
        
        if response.status_code == 200:
            data = response.json()
            rate = data.get("rates", {}).get(to_cur)
            if rate:
                return float(rate)
        
        return None
    except Exception as e:
        print(f"Error getting exchange rate: {e}")
        return None


def get_currencies_from_sheet() -> list[str]:
    """Gets list of unique currencies from Google Sheets."""
    try:
        all_records = sheet.get_all_records()
        if not all_records:
            return CURS  # Return default currencies if no data
        
        currencies = set()
        for record in all_records:
            currency = record.get("Currency") or record.get("Валюта")
            if currency:
                currencies.add(currency)
        
        # Return sorted list, or default if empty
        return sorted(list(currencies)) if currencies else CURS
    except Exception as e:
        print(f"Error getting currencies: {e}")
        return CURS


def get_last_n_records(n: int = 3, category: str = None) -> str:
    """Returns last N records from Google Sheets, optionally filtered by category."""
    try:
        all_records = sheet.get_all_records()
        if not all_records:
            return "📭 No records"
        
        # Filter by category if specified
        if category and category != "All":
            all_records = [r for r in all_records if r.get("Category") == category]
        
        if not all_records:
            return f"📭 No records for category: {category}"
        
        # Get headers to determine correct column name
        headers = sheet.row_values(1) if sheet.row_values(1) else []
        
        # Determine "Spender" column name - try different variants
        spender_key = None
        possible_keys = ["Кто внес", "Who", "Spender", "Кто", "Who внес"]
        for key in possible_keys:
            if key in headers:
                spender_key = key
                break
        
        # If not found by name, use index (6th column, index 5)
        if not spender_key and len(headers) > 5:
            all_values = sheet.get_all_values()
            if len(all_values) > 0:
                # Filter by category if needed
                if category and category != "All":
                    filtered_rows = []
                    for i, row in enumerate(all_values[1:], 1):  # Skip header
                        if len(row) > 2 and row[2] == category:
                            filtered_rows.append(row)
                    last_records = filtered_rows[-n:] if len(filtered_rows) > n else filtered_rows
                else:
                    last_records = all_values[-n:] if len(all_values) > n else all_values[1:]
                last_records.reverse()
                
                lines = [f"📋 Last {n} records:\n"]
                for i, row in enumerate(last_records, 1):
                    date = row[0] if len(row) > 0 else "?"
                    cat = row[2] if len(row) > 2 else "?"
                    # Handle amount conversion with comma/dot support
                    if len(row) > 3 and row[3]:
                        try:
                            amount = float(str(row[3]).replace(",", "."))
                        except (ValueError, TypeError):
                            amount = 0
                    else:
                        amount = 0
                    currency = row[4] if len(row) > 4 else "?"
                    spender = row[5] if len(row) > 5 else "?"
                    comment = row[6] if len(row) > 6 else ""
                    
                    comment_text = f" ({comment})" if comment else ""
                    lines.append(
                        f"{i}. 📅 {date} | {cat} | {amount:,.2f} {currency} | 👤 {spender}{comment_text}"
                    )
                
                return "\n".join(lines)
        
        # Get last N records
        last_records = all_records[-n:]
        last_records.reverse()  # Show newest first
        
        lines = [f"📋 Last {n} records:\n"]
        for i, record in enumerate(last_records, 1):
            date = record.get("Date", record.get("Дата", "?"))
            cat = record.get("Category", record.get("Категория", "?"))
            # Handle amount conversion with comma/dot support
            amount_raw = record.get("Amount", record.get("Сумма", 0))
            try:
                amount = float(str(amount_raw).replace(",", "."))
            except (ValueError, TypeError):
                amount = 0
            currency = record.get("Currency", record.get("Валюта", "?"))
            
            # Use found key or try different variants
            if spender_key:
                spender = record.get(spender_key, "?")
            else:
                spender = (record.get("Кто внес") or record.get("Who") or 
                          record.get("Spender") or record.get("Кто") or "?")
            
            comment = record.get("Comment", record.get("Комментарий", ""))
            
            comment_text = f" ({comment})" if comment else ""
            lines.append(
                f"{i}. 📅 {date} | {cat} | {amount:,.2f} {currency} | 👤 {spender}{comment_text}"
            )
        
        return "\n".join(lines)
    except Exception as e:
        return f"❌ Error getting records: {e}"


def compute_stats(cat, month=None, date_from=None, date_to=None, 
                 group_by_currency=True, convert_to_currency=None):
    """Computes statistics with support for custom periods and currency conversion.
    Returns tuple: (stats_text, conversion_details_dict)"""
    df = pd.DataFrame(sheet.get_all_records())
    conversion_details = {}
    
    # Normalize column names (support both English and Russian)
    column_mapping = {
        "Amount": "Amount",
        "Сумма": "Amount",
        "Currency": "Currency",
        "Валюта": "Currency",
        "Category": "Category",
        "Категория": "Category",
        "Date": "Date",
        "Дата": "Date",
        "Month": "Month",
        "Месяц": "Month"
    }
    
    # Rename columns to standard English names
    for old_name, new_name in column_mapping.items():
        if old_name in df.columns:
            df.rename(columns={old_name: new_name}, inplace=True)
    
    # Convert Amount column to float explicitly (handles string values from Google Sheets)
    # Replace commas with dots and convert to float
    if "Amount" not in df.columns:
        return "❌ Error: Amount column not found", {}
    
    df["Amount"] = pd.to_numeric(
        df["Amount"].astype(str).str.replace(",", "."), 
        errors='coerce'
    ).fillna(0)
    
    # Filter by period
    if month:
        if "Month" not in df.columns:
            return "❌ Error: Month column not found", {}
        df = df[df["Month"] == month]
    elif date_from and date_to:
        # Convert dates to datetime for comparison
        if "Date" not in df.columns:
            return "❌ Error: Date column not found", {}
        df["Date"] = pd.to_datetime(df["Date"], format=DATE_FMT, errors='coerce')
        date_from_dt = pd.to_datetime(date_from, format=DATE_FMT)
        date_to_dt = pd.to_datetime(date_to, format=DATE_FMT)
        df = df[(df["Date"] >= date_from_dt) & (df["Date"] <= date_to_dt)]
        df["Date"] = df["Date"].dt.strftime(DATE_FMT)
    
    if cat != "All":
        if "Category" not in df.columns:
            return "❌ Error: Category column not found", {}
        df = df[df["Category"] == cat]
    
    if df.empty:
        return "No data 🤷", {}
    
    # Currency conversion if specified
    if convert_to_currency:
        df_converted = df.copy()
        original_currencies = df["Currency"].unique()
        
        for currency in original_currencies:
            if currency != convert_to_currency:
                rate = get_exchange_rate(currency, convert_to_currency)
                if rate:
                    mask = df_converted["Currency"] == currency
                    original_amount = df_converted.loc[mask, "Amount"].sum()
                    df_converted.loc[mask, "Amount"] = df_converted.loc[mask, "Amount"] * rate
                    df_converted.loc[mask, "Currency"] = convert_to_currency
                    
                    # Store conversion details
                    conversion_details[currency] = {
                        "rate": rate,
                        "original_amount": original_amount,
                        "converted_amount": original_amount * rate
                    }
                else:
                    return f"❌ Failed to get exchange rate for {currency} → {convert_to_currency}", {}
        df = df_converted
    
    if group_by_currency:
        # Statistics grouped by currency
        total = df.groupby("Currency")["Amount"].sum()
        lines = [f"{cur}: {amt:,.2f}" for cur, amt in total.items()]
        return "\n".join(lines) if lines else "No data 🤷", conversion_details
    else:
        # Simple total without grouping
        total_overall = df["Amount"].sum()
        currency = convert_to_currency if convert_to_currency else df["Currency"].iloc[0] if len(df) > 0 else "?"
        return f"💰 Total: {total_overall:,.2f} {currency}", conversion_details


# ---------- Conversation steps ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [["💰 Add expense", "📊 Show statistics"]]
    kb.append(["🏠 To start"])

    # message may be None if CallbackQuery came
    if update.message:
        target = update.message
    else:
        target = update.callback_query.message

    await target.reply_text(
        "👋 Hi! What would you like to do?",
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True),
    )
    return CHOOSE_ACTION


async def choose_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    
    # Handle "To start" button
    if text == "🏠 To start" or text == "To start":
        context.user_data.clear()
        await start(update, context)
        return CHOOSE_ACTION
    
    if text == "💰 Add expense" or text == "Add expense":
        kb = [[c] for c in CATS]
        kb.append(["🏠 To start"])
        await update.message.reply_text(
            "📂 Choose category:", 
            reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True)
        )
        return CHOOSE_CAT
    elif text == "📊 Show statistics" or text == "Show statistics":
        # First ask: All categories or specific
        kb = [["All categories", "Specific category"]]
        kb.append(["🏠 To start"])
        await update.message.reply_text(
            "📊 Statistics for all categories or specific?", 
            reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True)
        )
        return STAT_CAT
    else:
        await update.message.reply_text("Please press a button 😉")
        return CHOOSE_ACTION


# ----- Add expense flow -----
async def choose_cat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    
    # Handle "To start" button
    if text == "🏠 To start" or text == "To start":
        context.user_data.clear()
        await start(update, context)
        return CHOOSE_ACTION
    
    context.user_data["cat"] = text
    kb = [["🏠 To start"]]
    await update.message.reply_text(
        "💵 Enter amount:",
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
    )
    return TYPING_AMT


async def type_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    
    # Handle "To start" button
    if text == "🏠 To start" or text == "To start":
        context.user_data.clear()
        await start(update, context)
        return CHOOSE_ACTION
    
    try:
        amt = float(text.replace(",", "."))
    except ValueError:
        await update.message.reply_text("❌ Need a number. Try again:")
        return TYPING_AMT
    context.user_data["amt"] = amt
    kb = [[c] for c in CURS]
    kb.append(["🏠 To start"])
    await update.message.reply_text(
        "💱 Currency?",
        reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True)
    )
    return CHOOSE_CUR


async def choose_cur(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    
    # Handle "To start" button
    if text == "🏠 To start" or text == "To start":
        context.user_data.clear()
        await start(update, context)
        return CHOOSE_ACTION
    
    context.user_data["cur"] = text
    
    # Automatically detect user
    user_name, username = get_user_info(update)
    context.user_data["spender"] = user_name
    
    # Go to comment
    buttons = [
        [InlineKeyboardButton("⏭️ Skip", callback_data="skip")],
        [InlineKeyboardButton("🏠 To start", callback_data="to_start")]
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
        if update.callback_query.data == "to_start":
            context.user_data.clear()
            await start(update, context)
            return CHOOSE_ACTION
        context.user_data["comment"] = ""
    else:
        text = update.message.text
        if text == "🏠 To start" or text == "To start":
            context.user_data.clear()
            await start(update, context)
            return CHOOSE_ACTION
        context.user_data["comment"] = text

    # Choose date
    buttons = [
        [InlineKeyboardButton("📅 Today", callback_data="today"),
         InlineKeyboardButton("📆 Yesterday", callback_data="yesterday")],
        [InlineKeyboardButton("📆 Enter date", callback_data="custom")],
        [InlineKeyboardButton("🏠 To start", callback_data="to_start")]
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
    
    if query.data == "to_start":
        context.user_data.clear()
        await start(update, context)
        return CHOOSE_ACTION
    
    if query.data == "today":
        date_str = datetime.now().strftime(DATE_FMT)
        await save_row(update, context, date_str)
        return CHOOSE_ACTION
    elif query.data == "yesterday":
        date_str = (datetime.now() - timedelta(days=1)).strftime(DATE_FMT)
        await save_row(update, context, date_str)
        return CHOOSE_ACTION
    else:
        await query.edit_message_text("📅 Enter date in format DD.MM.YYYY:")
        return TYPING_DT


async def type_dt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    
    # Handle "To start" button
    if text == "🏠 To start" or text == "To start":
        context.user_data.clear()
        await start(update, context)
        return CHOOSE_ACTION
    
    try:
        date_str = dparser.parse(text, dayfirst=True).strftime(DATE_FMT)
    except Exception:
        await update.message.reply_text("❌ Cannot parse date, try 13.07.2025")
        return TYPING_DT
    await save_row(update, context, date_str)
    return CHOOSE_ACTION


async def save_row(update: Update, context: ContextTypes.DEFAULT_TYPE, date_str: str):
    month_str = month_of(date_str)
    cat = context.user_data["cat"]
    amt = context.user_data["amt"]
    cur = context.user_data["cur"]
    who = context.user_data["spender"]
    cmnt = context.user_data.get("comment", "")
    sheet_append([date_str, month_str, cat, amt, cur, who, cmnt])

    text = f"✅ Saved: {cat} – {amt:.2f} {cur} on {date_str}"

    # Reply to chat depending on whether it was a message or button press
    if update.callback_query:
        await update.callback_query.edit_message_text(text)
    else:
        await update.message.reply_text(text)

    # Show main menu
    await start(update, context)
    return CHOOSE_ACTION


# ----- Stats flow -----
async def stat_cat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles category selection for statistics."""
    text = update.message.text
    
    # Handle "To start" button
    if text == "🏠 To start" or text == "To start":
        context.user_data.clear()
        await start(update, context)
        return CHOOSE_ACTION
    
    if text == "All categories":
        context.user_data["stat_cat"] = "All"
    elif text == "Specific category":
        # Show list of categories
        kb = [[c] for c in CATS]
        kb.append(["🏠 To start"])
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
    kb.append(["🏠 To start"])
    await update.message.reply_text(
        f"📊 Statistics type for category '{cat}':",
        reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True)
    )
    return STAT_TYPE


async def stat_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles statistics type selection."""
    text = update.message.text
    
    # Handle "To start" button
    if text == "🏠 To start" or text == "To start":
        context.user_data.clear()
        await start(update, context)
        return CHOOSE_ACTION
    
    if text == "📜 Last 3 records":
        # Show last 3 records immediately
        cat = context.user_data.get("stat_cat", "All")
        last_records = get_last_n_records(3, cat if cat != "All" else None)
        await update.message.reply_text(last_records)
        await start(update, context)
        return CHOOSE_ACTION
    
    elif text == "📅 Custom period":
        # Ask for start date
        kb = [["🏠 To start"]]
        await update.message.reply_text(
            "📅 Enter start date (DD.MM.YYYY):",
            reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
        )
        return STAT_DATE_FROM
    
    elif text == "📆 By months":
        # Show list of months
        now = datetime.now()
        months = [(now.replace(day=1) - pd.DateOffset(months=i)).strftime(MONTH_FMT) for i in range(12)]
        kb = [[m] for m in months]
        kb.append(["🏠 To start"])
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
    
    if text == "🏠 To start" or text == "To start":
        context.user_data.clear()
        await start(update, context)
        return CHOOSE_ACTION
    
    try:
        date_from = dparser.parse(text, dayfirst=True).strftime(DATE_FMT)
        context.user_data["stat_date_from"] = date_from
        kb = [["🏠 To start"]]
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
    
    if text == "🏠 To start" or text == "To start":
        context.user_data.clear()
        await start(update, context)
        return CHOOSE_ACTION
    
    try:
        date_to = dparser.parse(text, dayfirst=True).strftime(DATE_FMT)
        context.user_data["stat_date_to"] = date_to
        
        # Ask about currency grouping
        kb = [["Yes", "No"]]
        kb.append(["🏠 To start"])
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
    
    if text == "🏠 To start" or text == "To start":
        context.user_data.clear()
        await start(update, context)
        return CHOOSE_ACTION
    
    context.user_data["stat_month"] = text
    
    # Ask about currency grouping
    kb = [["Yes", "No"]]
    kb.append(["🏠 To start"])
    await update.message.reply_text(
        "💱 Group by currencies?",
        reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True)
    )
    return STAT_GROUP_CURRENCY


async def stat_group_currency(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles currency grouping choice."""
    text = update.message.text
    
    if text == "🏠 To start" or text == "To start":
        context.user_data.clear()
        await start(update, context)
        return CHOOSE_ACTION
    
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
        kb.append(["🏠 To start"])
        await update.message.reply_text(
            "💱 Convert all expenses to which currency?",
            reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True)
        )
        return STAT_CONVERT_CURRENCY


async def stat_convert_currency(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles currency selection for conversion."""
    text = update.message.text
    
    if text == "🏠 To start" or text == "To start":
        context.user_data.clear()
        await start(update, context)
        return CHOOSE_ACTION
    
    context.user_data["stat_convert_to"] = text
    
    # Show statistics and ask about details
    await show_statistics_result(update, context, ask_details=True)
    return STAT_SHOW_DETAILS


async def stat_show_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles showing conversion details or finishing."""
    text = update.message.text
    
    if text == "🏠 To start" or text == "To start":
        context.user_data.clear()
        await start(update, context)
        return CHOOSE_ACTION
    
    if text == "Show details":
        # Show conversion details
        conversion_details = context.user_data.get("conversion_details", {})
        if conversion_details:
            lines = ["💱 Conversion details:\n"]
            for from_cur, details in conversion_details.items():
                lines.append(
                    f"{from_cur} → {context.user_data.get('stat_convert_to', '?')}:\n"
                    f"  Rate: {details['rate']:.4f}\n"
                    f"  Original: {details['original_amount']:,.2f} {from_cur}\n"
                    f"  Converted: {details['converted_amount']:,.2f} {context.user_data.get('stat_convert_to', '?')}\n"
                )
            await update.message.reply_text("\n".join(lines))
        else:
            await update.message.reply_text("No conversion details available")
    
    await start(update, context)
    return CHOOSE_ACTION


async def show_statistics_result(update: Update, context: ContextTypes.DEFAULT_TYPE, ask_details: bool = False):
    """Shows statistics result and optionally asks for details."""
    cat = context.user_data.get("stat_cat", "All")
    group_by_currency = context.user_data.get("stat_group_currency", True)
    convert_to = context.user_data.get("stat_convert_to")
    
    # Determine period
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
    
    # Store conversion details for later use
    if conversion_details:
        context.user_data["conversion_details"] = conversion_details
    
    # Build result text
    if convert_to:
        stats_text = f"📊 Statistics {period_text}, category '{cat}' (in {convert_to}):\n{stats}"
    else:
        stats_text = f"📊 Statistics {period_text}, category '{cat}':\n{stats}"
    
    await update.message.reply_text(stats_text)
    
    if ask_details and conversion_details:
        # Ask if user wants to see details
        kb = [["Show details", "Done (thanks)"]]
        kb.append(["🏠 To start"])
        await update.message.reply_text(
            "Would you like to see conversion details?",
            reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True)
        )
    else:
        await start(update, context)


async def show_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows current categories."""
    if CATS:
        text = f"📋 Current categories ({len(CATS)}):\n{', '.join(CATS)}"
    else:
        text = "❌ Categories not loaded"
    
    await update.message.reply_text(text)


async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows information about current user."""
    user = update.effective_user
    user_id = user.id
    username = user.username or user.first_name or f"User{user_id}"
    
    # Check if user is registered
    if user_id in TELEGRAM_USERS:
        registered_name = TELEGRAM_USERS[user_id]
        text = f"👤 Your profile:\nID: {user_id}\nName: {username}\nRegistered as: {registered_name}"
    else:
        text = f"👤 Your profile:\nID: {user_id}\nName: {username}\nStatus: Not registered\n\nUse /register to register"
    
    await update.message.reply_text(text)


async def register_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Registers user in the system."""
    user = update.effective_user
    user_id = user.id
    username = user.username or user.first_name or f"User{user_id}"
    
    # Check if command has arguments
    if context.args:
        name = context.args[0]
        # In real app, save to database or file
        # For now, just show information
        text = f"✅ Registration:\nID: {user_id}\nName: {username}\nRegistered as: {name}\n\n⚠️  To save, add to code:\nTELEGRAM_USERS[{user_id}] = \"{name}\""
    else:
        text = f"📝 User registration:\n\nUse: /register NAME\n\nExample: /register Lisa\n\nYour ID: {user_id}\nYour name: {username}"
    
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
    """Reloads categories from Google Sheets."""
    global CATS
    old_cats = CATS.copy()
    CATS = load_categories()
    
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
    context.user_data.clear()  # clear all accumulated data
    await update.message.reply_text(
        "❌ Action cancelled. Starting over 🙂"
    )
    # Show the same keyboard as in start()
    await start(update, context)
    return CHOOSE_ACTION


# ---------- Main ----------
def main():
    # Get token from environment variables
    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        raise RuntimeError("Environment variable BOT_TOKEN not found.")

    # Initialize Google Sheets connection
    global sheet
    sheet = open_sheet()

    app = Application.builder().token(bot_token).build()

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

    app.add_handler(conv)
    app.add_handler(CommandHandler("reloadcats", reload_cats))
    app.add_handler(CommandHandler("categories", show_categories))
    app.add_handler(CommandHandler("test_connection", test_connection))
    app.add_handler(CommandHandler("whoami", whoami))
    app.add_handler(CommandHandler("register", register_user))

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
