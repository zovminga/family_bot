# Family Bot 💰

Telegram bot for family expense tracking with Google Sheets integration.

## Features

- Add expenses by category
- Multi-currency support (₽, дин, €, ¥)
- Auto user detection by Telegram ID
- Statistics: last 3 records, custom period, by months
- Currency conversion with exchange rates
- Group by currencies or convert to single currency
- Dynamic category loading from Google Sheets

## Commands

- `/start` - Start bot
- `/categories` - Show categories
- `/reloadcats` - Reload categories
- `/test_connection` - Test Google Sheets connection
- `/whoami` - Show profile
- `/register NAME` - Register user
- `/cancel` or `/stop` - Cancel current action

## Quick Start

### 1. Environment Variables

```bash
BOT_TOKEN=your_telegram_bot_token
GOOGLE_CREDS_PATH=/path/to/credentials.json
SHEET_NAME=your_google_sheet_name
RENDER_EXTERNAL_URL=https://your-app.onrender.com  # for Render deployment
```

### 2. Google Sheets Setup

**Config sheet** (column A):
- A1: Header (e.g., "Categories")
- A2+: Category names

**Data sheet** columns:
- Date (DD.MM.YYYY)
- Month (YYYY-MM)
- Category
- Amount
- Currency
- Who (spender name)
- Comment

### 3. Google Cloud Setup

1. Create project in Google Cloud Console
2. Enable Google Sheets API
3. Create service account
4. Download JSON credentials
5. Grant sheet access to service account email

### 4. Installation

```bash
pip install -r requirements.txt
python bot.py
```

### 5. Deploy to Render

1. Connect repository
2. Set environment variables
3. Start command: `python bot.py`
4. Port: `8443`

## User Setup

1. Get Telegram ID: `/whoami`
2. Add to `bot.py`:
```python
TELEGRAM_USERS = {
    123456789: "Lisa",
    987654321: "Azat",
}
```

## Statistics Flow

1. Choose category: All or Specific
2. Choose type: Last 3 records / Custom period / By months
3. Choose grouping: Group by currencies (Yes/No)
4. If No: Select currency to convert to (from Google Sheets)
5. View results with optional conversion details

## Troubleshooting

- **Categories not loading**: Check "Config" sheet name, use `/test_connection`
- **Connection error**: Verify credentials path and service account access
- **Currency rates**: Check internet, exchangerate-api.com (free, no key needed)
- **Bot not responding**: Check `BOT_TOKEN`, verify bot is running

## License

MIT License
