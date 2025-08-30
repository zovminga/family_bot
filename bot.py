#!/usr/bin/env python3
import os
import json
from datetime import datetime
from dateutil import parser as dparser

import pandas as pd
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
    """Тестирует подключение к Google Sheets."""
    try:
        open_sheet("Config")
        print("✅ Подключение к Google Sheets успешно")
        return True
    except Exception as e:
        print(f"❌ Ошибка подключения к Google Sheets: {e}")
        return False


def open_sheet(sheet_name="Data"):
    scope = ["https://www.googleapis.com/auth/drive",
             "https://www.googleapis.com/auth/spreadsheets"]

    # Получаем путь к файлу из переменной окружения
    creds_path = os.getenv("GOOGLE_CREDS_PATH")
    if not creds_path:
        raise RuntimeError("Переменная окружения GOOGLE_CREDS_PATH не найдена.")

    # Авторизуемся, используя файл по указанному пути
    creds = ServiceAccountCredentials.from_json_keyfile_name(creds_path, scope)

    gc = gspread.authorize(creds)
    sheet_name_env = os.getenv("SHEET_NAME")
    if not sheet_name_env:
        raise RuntimeError("Переменная окружения SHEET_NAME не найдена.")

    sh = gc.open(sheet_name_env)
    return sh.worksheet(sheet_name)


def validate_categories(categories: list[str]) -> list[str]:
    """Валидирует и очищает список категорий."""
    if not categories:
        return []
    
    # Убираем дубликаты, сохраняя порядок
    seen = set()
    unique_categories = []
    for cat in categories:
        cat_clean = cat.strip()
        if cat_clean and cat_clean not in seen:
            seen.add(cat_clean)
            unique_categories.append(cat_clean)
    
    return unique_categories


def load_categories() -> list[str]:
    """Читает список категорий из столбца A листа Config."""
    try:
        cfg_ws = open_sheet("Config")  # ← название листа, где лежит список
        col = cfg_ws.col_values(1)  # A:A
        col = [c.strip() for c in col if c.strip()]  # убираем пустые
        categories = col[1:] if len(col) > 1 else []  # отбрасываем заголовок
        
        # Валидируем категории
        categories = validate_categories(categories)
        
        # Проверяем, что категории не пустые
        if not categories:
            print("⚠️  Внимание: лист Config пуст или не содержит категорий")
            return []
            
        print(f"✅ Загружено {len(categories)} категорий: {', '.join(categories)}")
        return categories
        
    except Exception as e:
        print(f"❌ Ошибка при загрузке категорий: {e}")
        # Возвращаем базовые категории в случае ошибки
        return ["Продукты", "Транспорт", "Развлечения", "Другое"]


sheet = open_sheet()

# ---------- Bot constants ----------
def initialize_categories():
    """Инициализирует категории при запуске бота."""
    global CATS
    
    # Тестируем подключение к Google Sheets
    if not test_google_sheets_connection():
        print("⚠️  Используются базовые категории из-за проблем с подключением")
        CATS = ["Продукты", "Транспорт", "Развлечения", "Другое"]
        return CATS
    
    CATS = load_categories()
    if not CATS:
        print("⚠️  Используются базовые категории")
        CATS = ["Продукты", "Транспорт", "Развлечения", "Другое"]
    return CATS

CATS = initialize_categories()
CURS = ["RUB", "RSD", "EUR"]
MONTH_FMT = "%Y-%m"
DATE_FMT = "%d.%m.%Y"
SPENDERS = ["Лиза", "Азат"]

# Словарь для сопоставления Telegram ID с именами пользователей
# Замените на реальные Telegram ID пользователей
TELEGRAM_USERS = {
    # Пример: 123456789: "Лиза",
    248826020: "Азат",
}
(
    CHOOSE_ACTION, CHOOSE_CAT, TYPING_AMT, CHOOSE_CUR,
    TYPING_CMNT,
    CHOOSE_DT, TYPING_DT,
    STAT_CAT, STAT_MON, STAT_GROUP, STAT_GROUP_CAT
) = range(11)


# -------- Helpers ----------
def month_of(date_str: str) -> str:
    return datetime.strptime(date_str, DATE_FMT).strftime(MONTH_FMT)


def get_user_info(update: Update) -> tuple[str, str]:
    """Получает информацию о пользователе из Telegram."""
    user = update.effective_user
    user_id = user.id
    username = user.username or user.first_name or f"User{user_id}"
    
    # Проверяем, есть ли пользователь в словаре
    if user_id in TELEGRAM_USERS:
        return TELEGRAM_USERS[user_id], username
    else:
        # Если пользователь не найден, возвращаем его имя из Telegram
        return username, username


def sheet_append(row):
    sheet.append_row(row, value_input_option="USER_ENTERED")


def compute_stats(cat, month, group_by_category=False):
    df = pd.DataFrame(sheet.get_all_records())
    df = df[df["Month"] == month]
    if cat != "Все":
        df = df[df["Category"] == cat]
    
    if df.empty:
        return "Нет данных 🤷"
    
    if not group_by_category:
        # Обычная статистика по валютам
        total = df.groupby("Currency")["Amount"].sum()
        lines = [f"{cur}: {amt:,.2f}" for cur, amt in total.items()]
        return "\n".join(lines) if lines else "Нет данных 🤷"
    else:
        # Группировка по категориям и валютам
        # Группируем по категории и валюте
        grouped = df.groupby(["Category", "Currency"])["Amount"].sum().reset_index()
        
        # Сортируем категории по общей сумме
        category_totals = df.groupby("Category")["Amount"].sum().sort_values(ascending=False)
        
        # Формируем результат
        result_lines = []
        total_overall = df["Amount"].sum()
        result_lines.append(f"💰 Общая сумма: {total_overall:,.2f}")
        result_lines.append("")
        
        for category in category_totals.index:
            cat_data = grouped[grouped["Category"] == category]
            cat_total = category_totals[category]
            
            # Строки для каждой валюты в категории
            currency_lines = []
            for _, row in cat_data.iterrows():
                currency_lines.append(f"  {row['Currency']}: {row['Amount']:,.2f}")
            
            # Процент от общей суммы
            percentage = (cat_total / total_overall) * 100 if total_overall > 0 else 0
            
            result_lines.append(f"📊 {category} ({percentage:.1f}%):")
            result_lines.extend(currency_lines)
            result_lines.append(f"  💵 Итого: {cat_total:,.2f}")
            result_lines.append("")  # Пустая строка между категориями
        
        return "\n".join(result_lines).strip()


# ---------- Conversation steps ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [["Добавить трату", "Показать статистику"]]

    # message может быть None, если пришёл CallbackQuery
    if update.message:
        target = update.message
    else:
        target = update.callback_query.message  # сообщение, на которое кликнули

    await target.reply_text(
        "Привет! Что делаем?",
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True),
    )
    return CHOOSE_ACTION


async def choose_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "Добавить трату":
        kb = [[c] for c in CATS]
        await update.message.reply_text(
            "Выберите категорию:", reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True)
        )
        return CHOOSE_CAT
    elif text == "Показать статистику":
        kb = [[c] for c in ["Все"] + CATS]
        await update.message.reply_text(
            "По какой категории?", reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True)
        )
        return STAT_CAT
    else:
        await update.message.reply_text("Нажмите кнопку 😉")
        return CHOOSE_ACTION


# ----- Add expense flow -----
async def choose_cat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["cat"] = update.message.text
    await update.message.reply_text("Введите сумму:")
    return TYPING_AMT


async def type_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amt = float(update.message.text.replace(",", "."))
    except ValueError:
        await update.message.reply_text("Нужно число. Попробуем ещё раз:")
        return TYPING_AMT
    context.user_data["amt"] = amt
    kb = [[c] for c in CURS]
    await update.message.reply_text("Валюта?",
                                    reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True))
    return CHOOSE_CUR


async def choose_cur(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["cur"] = update.message.text
    
    # Автоматически определяем пользователя
    user_name, username = get_user_info(update)
    context.user_data["spender"] = user_name
    
    # Сразу переходим к комментарию
    buttons = [
        [InlineKeyboardButton("Пропустить", callback_data="skip")]
    ]
    await update.message.reply_text(
        f"👤 Автоматически определен: {user_name}\n\nДобавьте комментарий или нажмите «Пропустить»",
        reply_markup=InlineKeyboardMarkup(buttons)
    )
    return TYPING_CMNT


async def type_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Этот обработчик вызывается и для текстовых сообщений, и для нажатия кнопки "skip"
    if update.callback_query:
        # Нажали «Пропустить»
        await update.callback_query.answer()
        context.user_data["comment"] = ""
    else:
        context.user_data["comment"] = update.message.text

    # далее выбираем дату
    buttons = [
        [InlineKeyboardButton("Сегодня", callback_data="today"),
         InlineKeyboardButton("Указать дату", callback_data="custom")]
    ]
    # Используем update.effective_message для ответа, т.к. может быть и Message, и CallbackQuery
    await update.effective_message.reply_text("Дата траты:", reply_markup=InlineKeyboardMarkup(buttons))
    return CHOOSE_DT


async def choose_dt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "today":
        date_str = datetime.now().strftime(DATE_FMT)
        await save_row(update, context, date_str)
        return CHOOSE_ACTION
    else:
        await query.edit_message_text("Введите дату в формате дд.мм.гггг:")
        return TYPING_DT


async def type_dt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        date_str = dparser.parse(update.message.text, dayfirst=True).strftime(DATE_FMT)
    except Exception:
        await update.message.reply_text("Не могу разобрать дату, попробуйте 13.07.2025")
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

    text = f"✅ Записал: {cat} – {amt:.2f} {cur} за {date_str}"

    # Отвечаем в чат в зависимости от того, было это сообщение или нажатие кнопки
    if update.callback_query:
        await update.callback_query.edit_message_text(text)
    else:
        await update.message.reply_text(text)

    # Сразу показываем главное меню
    await start(update, context)
    return CHOOSE_ACTION


# ----- Stats flow -----
async def stat_cat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["stat_cat"] = update.message.text
    # список последних 12 месяцев
    now = datetime.now()
    months = [(now.replace(day=1) - pd.DateOffset(months=i)).strftime(MONTH_FMT) for i in range(12)]
    kb = [[m] for m in months]
    await update.message.reply_text("За какой месяц?",
                                    reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True))
    return STAT_MON


async def stat_mon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["stat_month"] = update.message.text
    kb = [["Да", "Нет"]]
    await update.message.reply_text(
        "Нужна ли группировка по категориям?",
        reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True)
    )
    return STAT_GROUP


async def stat_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает выбор группировки."""
    choice = update.message.text
    month = context.user_data["stat_month"]
    cat = context.user_data["stat_cat"]
    
    if choice == "Да":
        # Если выбрана конкретная категория, группировка не нужна
        if cat != "Все":
            stats = compute_stats(cat, month, group_by_category=False)
            await update.message.reply_text(
                f"Статистика за {month}, категория {cat}:\n{stats}"
            )
        else:
            # Для "Все" категорий показываем группировку
            stats = compute_stats(cat, month, group_by_category=True)
            await update.message.reply_text(
                f"Статистика за {month} (группировка по категориям):\n{stats}"
            )
    else:
        # Обычная статистика без группировки
        stats = compute_stats(cat, month, group_by_category=False)
        await update.message.reply_text(
            f"Статистика за {month}, категория {cat}:\n{stats}"
        )
    
    # Возвращаемся в главное меню
    await start(update, context)
    return CHOOSE_ACTION


async def show_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает текущие категории."""
    if CATS:
        text = f"📋 Текущие категории ({len(CATS)} шт.):\n{', '.join(CATS)}"
    else:
        text = "❌ Категории не загружены"
    
    await update.message.reply_text(text)


async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает информацию о текущем пользователе."""
    user = update.effective_user
    user_id = user.id
    username = user.username or user.first_name or f"User{user_id}"
    
    # Проверяем, зарегистрирован ли пользователь
    if user_id in TELEGRAM_USERS:
        registered_name = TELEGRAM_USERS[user_id]
        text = f"👤 Ваш профиль:\nID: {user_id}\nИмя: {username}\nЗарегистрирован как: {registered_name}"
    else:
        text = f"👤 Ваш профиль:\nID: {user_id}\nИмя: {username}\nСтатус: Не зарегистрирован\n\nИспользуйте /register для регистрации"
    
    await update.message.reply_text(text)


async def register_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Регистрирует пользователя в системе."""
    user = update.effective_user
    user_id = user.id
    username = user.username or user.first_name or f"User{user_id}"
    
    # Проверяем, есть ли аргументы в команде
    if context.args:
        name = context.args[0]
        # В реальном приложении здесь нужно сохранить в базу данных или файл
        # Пока просто выводим информацию
        text = f"✅ Регистрация:\nID: {user_id}\nИмя: {username}\nЗарегистрирован как: {name}\n\n⚠️  Для сохранения добавьте в код:\nTELEGRAM_USERS[{user_id}] = \"{name}\""
    else:
        text = f"📝 Регистрация пользователя:\n\nИспользуйте: /register ИМЯ\n\nПример: /register Лиза\n\nВаш ID: {user_id}\nВаше имя: {username}"
    
    await update.message.reply_text(text)


async def test_connection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Тестирует подключение к Google Sheets."""
    try:
        # Тестируем подключение
        if test_google_sheets_connection():
            # Пытаемся загрузить категории
            test_cats = load_categories()
            if test_cats:
                text = f"✅ Подключение успешно!\n📋 Доступно категорий: {len(test_cats)}\n{', '.join(test_cats)}"
            else:
                text = "⚠️  Подключение работает, но категории не найдены в листе Config"
        else:
            text = "❌ Ошибка подключения к Google Sheets"
    except Exception as e:
        text = f"❌ Ошибка: {str(e)}"
    
    await update.message.reply_text(text)


async def reload_cats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Перезагружает категории из Google Sheets."""
    global CATS
    old_cats = CATS.copy()
    CATS = load_categories()
    
    if CATS:
        if old_cats == CATS:
            text = f"✅ Категории уже актуальны ({len(CATS)} шт.):\n{', '.join(CATS)}"
        else:
            text = f"🔄 Категории обновлены ({len(CATS)} шт.):\n{', '.join(CATS)}"
    else:
        text = "❌ Не удалось загрузить категории. Проверьте лист Config в Google Sheets."
    
    await update.message.reply_text(text)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Сбрасывает временные данные и возвращает пользователя
    к выбору действия.
    """
    context.user_data.clear()  # очищаем всё накопленное
    await update.message.reply_text(
        "Действие отменено. Начинаем заново 🙂"
    )
    # Показываем ту же клавиатуру, что и в start()
    await start(update, context)
    return CHOOSE_ACTION


# ---------- Main ----------
def main():
    # Получаем токен из переменных окружения
    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        raise RuntimeError("Переменная окружения BOT_TOKEN не найдена.")

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
                CallbackQueryHandler(type_comment, pattern="^skip$")
            ],
            CHOOSE_DT: [CallbackQueryHandler(choose_dt)],
            TYPING_DT: [MessageHandler(filters.TEXT & ~filters.COMMAND, type_dt)],
            STAT_CAT: [MessageHandler(filters.TEXT & ~filters.COMMAND, stat_cat)],
            STAT_MON: [MessageHandler(filters.TEXT & ~filters.COMMAND, stat_mon)],
            STAT_GROUP: [MessageHandler(filters.TEXT & ~filters.COMMAND, stat_group)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CommandHandler("stop", cancel),
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
    # Порт для Render.com
    port = int(os.environ.get('PORT', 8443))
    # URL, предоставленный Render.com
    render_url = os.getenv("RENDER_EXTERNAL_URL")

    if not render_url:
        print("Переменная RENDER_EXTERNAL_URL не найдена, запуск в режиме polling для локальной разработки.")
        app.run_polling()
    else:
        print(f"Запуск в режиме webhook, URL: {render_url}")
        # Запускаем webhook. Токен используется как секретный путь в URL.
        app.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path=bot_token,
            webhook_url=f"{render_url}/{bot_token}"
        )


if __name__ == "__main__":
    main()
