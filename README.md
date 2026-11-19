# Family Bot 💰

Telegram bot for household expense tracking with Google Sheets.

Each household **installs their own copy** (own bot token, own spreadsheet,
own allowlist). Your finances never mix with someone else's install.

**Agents / AI coding:** start at [`AGENTS.md`](AGENTS.md) → living state in
[`docs/STATUS.md`](docs/STATUS.md). Architecture map: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Features

- Add expenses by category (manual, typed text like «кофе 350», voice, receipt photo)
- Multi-currency (дин, €, ₽, ¥, £, $, CNY; list in `currency.py`)
- Statistics and optional HTML dashboard
- Categories and presets loaded from Google Sheets
- Allowlist so only your family can use *your* bot

## Install from scratch

### 1. Clone and Python deps

Python 3.12 (pinned in `.python-version`). Dependencies live in `pyproject.toml`
(groups `bot`, `dashboard`, `dev`) and are locked in `uv.lock`. Install
[uv](https://docs.astral.sh/uv/getting-started/installation/), then:

```bash
git clone <this-repo>
cd family_bot
uv sync                    # creates .venv with Python 3.12 and all groups,
                           # installs the family_bot package (family-bot command)
source .venv/bin/activate  # Windows: .venv\Scripts\activate
cp .env.example .env
```

Checks (the same ones CI runs on every pull request, see `.github/workflows/ci.yml`):

```bash
pytest
ruff check .
mypy                       # library modules listed in pyproject.toml
pip-audit --local
```

`requirements.txt` (bot) and `dashboard/requirements.txt` are generated from
`uv.lock` for pip-only hosts. Do not edit them by hand; the regenerate command
is in each file's header.

### 2. Telegram bot

1. Open [@BotFather](https://t.me/BotFather) → `/newbot` → copy the token
2. Put it in `.env` as `BOT_TOKEN=...`

### 3. Google Sheet + service account

1. [Google Cloud Console](https://console.cloud.google.com/) → create a project
2. Enable **Google Sheets API** and **Google Drive API**
3. Create a **service account** → download JSON key
4. Save it as `secrets/creds.json` (folder is gitignored)
5. Create an empty Google Spreadsheet (any name, e.g. `FamilyExpenses`)
6. **Share** the sheet with the service account email (Editor)
7. In `.env`:

```bash
GOOGLE_CREDS_PATH=secrets/creds.json
SHEET_NAME=FamilyExpenses
```

8. Create the worksheets and headers:

```bash
python scripts/bootstrap_sheet.py
```

This adds `Data`, `Config`, `Regular_expenses`, and `Meta` with defaults.
Safe to run again — it will not wipe existing rows.

### 4. Allow your family (required)

Start the bot once (even with empty allowlist, even before Google Sheets works):

```bash
family-bot                 # or: python -m family_bot.bot
```

At startup the bot prints one line per setup problem: what is wrong and what to
do (for example `⚠️ SHEET_NAME is not set — set it in .env …`). Until Google
Sheets opens, `/whoami` works and every other message lists those problems.

In Telegram, open your bot → `/whoami` → copy your numeric ID.
Add household members to `.env`:

```bash
ALLOWED_USERS=111111111:Azat,222222222:Lisa
```

Restart the bot. Anyone not on this list is rejected (they cannot read or
write your sheet). `/whoami` still works so new members can send you their ID.
A name may contain a comma: `111111111:Иван, Петров` is one entry.

### 5. Optional: voice, typed text & receipt photos

Set `ANTHROPIC_API_KEY` in `.env`. Without it, manual entry and presets still work.

### 6. Deploy (optional, e.g. Render)

1. Connect the repo
2. Set the same env vars (`BOT_TOKEN`, `GOOGLE_CREDS_PATH` contents or secret file,
   `SHEET_NAME`, `ALLOWED_USERS`, …)
3. Build command (installs the package and downloads the Vosk speech model
   into `models/`, so the first voice message does not wait for it):

   ```bash
   pip install -r requirements.txt && pip install --no-deps -e . && python -c "from family_bot.voice_input import ensure_model; ensure_model()"
   ```

   Keep `-e` (editable install): `.env`, `models/` and a relative
   `GOOGLE_CREDS_PATH` resolve from the repo checkout, not from `site-packages`.
   Render takes the Python version from `.python-version` unless the service sets a `PYTHON_VERSION` env var
4. Start command: `family-bot`
5. Set `RENDER_EXTERNAL_URL` for webhook mode (omit locally to use polling).
   Telegram posts updates to `<RENDER_EXTERNAL_URL>/telegram`; the bot token
   is not in the address.
   Set `WEBHOOK_SECRET` to a random string of 1–256 characters `A–Z a–z 0–9 _ -`:

   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

   Telegram sends it with every update; the bot answers 403 to requests
   without it. If it is unset, the bot makes a random secret at each start and
   registers it with Telegram, so it still works; a fixed value avoids rejected
   updates while an old and a new instance overlap during a deploy.
6. Optional: `TZ_NAME` — the family's IANA time zone (default `Europe/Belgrade`).
   Render runs in UTC; the bot takes "today" and "yesterday" in this zone.

## Commands

| Command | Who | Purpose |
|---------|-----|---------|
| `/start` | allowlisted | Main menu |
| `/whoami` | anyone | Show Telegram ID / allowlist status |
| `/register NAME` | allowlisted | Print an `ALLOWED_USERS=` line to copy into `.env` (does not save by itself) |
| `/dashboard` | allowlisted | Re-send latest HTML dashboard |
| `/categories` | allowlisted | List categories |
| `/reloadcats` | allowlisted | Reload categories + presets |
| `/test_connection` | allowlisted | Ping Google Sheets |
| `/cancel` `/stop` | allowlisted | Abort current flow |

## Dashboard

The bot does **not** build the dashboard. Build it on a laptop and publish
`file_id` into the `Meta` sheet — see [`dashboard/README.md`](dashboard/README.md).

## Sheet layout (after bootstrap)

**Config** — column A: header + category names  
**Data** — Date, Month, Category, Amount, Currency, Who, Comment  
**Regular_expenses** — preset templates (optional)  
**Meta** — dashboard `file_id` for `/dashboard`

## Troubleshooting

- **Access denied in Telegram** — your ID is missing from `ALLOWED_USERS`; use `/whoami`
- **Categories not loading** — run `python scripts/bootstrap_sheet.py`, then `/test_connection`
- **Connection error** — read the `⚠️` lines the bot prints at startup; check `GOOGLE_CREDS_PATH`, sheet name, and that the sheet is shared with the service account. `python scripts/test_google_sheets.py` reads `.env` and checks the connection
- **Bot not responding** — check `BOT_TOKEN`; locally omit `RENDER_EXTERNAL_URL` for polling

## License

MIT License
