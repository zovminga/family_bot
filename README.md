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

You need: a computer with a terminal (macOS or Linux; on Windows use WSL), a Telegram
account, a Google account. Steps 1–4 run the bot on your computer. Step 6
moves it to a server (Render) so it works when the computer is off.

### 1. Clone and Python deps

The bot runs on **Python 3.12** (pinned in `.python-version`). Dependencies live
in `pyproject.toml` (groups `bot`, `dashboard`, `dev`) and are locked in `uv.lock`.

Install [uv](https://docs.astral.sh/uv/getting-started/installation/). You do
not need to install Python yourself: `uv sync` downloads Python 3.12 if the
computer does not have it.

```bash
git clone https://github.com/zovminga/family_bot.git   # or your fork's URL
cd family_bot
uv sync                    # creates .venv with Python 3.12 and all groups,
                           # installs the family_bot package (family-bot command)
source .venv/bin/activate  # Windows: .venv\Scripts\activate
cp .env.example .env
```

After `source .venv/bin/activate` the commands `python`, `family-bot` and
`pytest` come from `.venv`. Run it again in every new terminal window.

**Without uv** (bot only, no test tools): install Python 3.12 yourself, then

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install --no-deps -e .
```

Checks (the same ones CI runs on every pull request, see `.github/workflows/ci.yml`;
they need the `uv sync` install):

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

The bot logs in to Google as a **service account** — a separate Google
identity for programs. It has its own email and a JSON key file.

1. [Google Cloud Console](https://console.cloud.google.com/) → project list
   at the top → **New project** → any name → **Create**. Select the new project.
2. **APIs & Services → Library** → find and **Enable** both
   **Google Sheets API** and **Google Drive API**
   (the bot finds the spreadsheet by its title through Drive).
3. **APIs & Services → Credentials → Create credentials → Service account** →
   any name → **Create and continue** → skip the optional role and user steps → **Done**.
4. In the **Service accounts** list click the new account → **Keys** tab →
   **Add key → Create new key → JSON → Create**. The browser downloads a `.json` file.
   Google gives this file only once; if you lose it, create a new key the same way.
5. Move the file into the repo as `secrets/creds.json` (create the `secrets`
   folder; it is gitignored, never commit it):

   ```bash
   mkdir -p secrets
   mv ~/Downloads/<downloaded-file>.json secrets/creds.json
   ```

6. Find the service account email. It is the `client_email` field in
   `secrets/creds.json`, for example
   `"client_email": "family-bot@my-project.iam.gserviceaccount.com"`.
   The same email is on the service account page in the Console (**Email**).
7. In [Google Sheets](https://sheets.google.com/) create an empty spreadsheet
   and give it a title, e.g. `FamilyExpenses`.
8. **Share** (top right) → paste the `client_email` → role **Editor** → **Send**.
   Without this the bot cannot open the spreadsheet.
9. In `.env`:

   ```bash
   GOOGLE_CREDS_PATH=secrets/creds.json
   SHEET_NAME=FamilyExpenses
   ```

   `SHEET_NAME` must match the spreadsheet title exactly (letters, case,
   spaces). The bot opens the spreadsheet by this title.
   `GOOGLE_CREDS_PATH` is a path to the key file, not the file contents.
   A relative path is read from the repo folder.

10. Create the worksheets and headers:

    ```bash
    python scripts/bootstrap_sheet.py
    ```

    This adds `Data`, `Config`, `Regular_expenses`, and `Meta` with defaults
    (these worksheet names are fixed in the code; do not rename them).
    Safe to run again — it will not wipe existing rows.
    To check the connection only: `python scripts/test_google_sheets.py`.

### 4. Allow your family (required)

Start the bot once (even with empty allowlist, even before Google Sheets works):

```bash
family-bot                 # or: python -m family_bot.bot
```

At startup the bot prints one line per setup problem: what is wrong and what to
do (for example `⚠️ SHEET_NAME is not set — set it in .env …`). Until Google
Sheets opens, `/whoami` works and every other message lists those problems.
A wrong `BOT_TOKEN` stops the bot before these lines, with
`telegram.error.InvalidToken` at the end of the output — copy the token from
@BotFather again.

In Telegram, open your bot → `/whoami` → copy your numeric ID.
Add household members to `.env`:

```bash
ALLOWED_USERS=111111111:Ann,222222222:Bob
```

Restart the bot (Ctrl+C, then `family-bot`). Anyone not on this list is
rejected (they cannot read or write your sheet). `/whoami` still works so new
members can send you their ID.
A name may contain a comma: `111111111:Иван, Петров` is one entry.

### 5. Optional: voice, typed text & receipt photos

These three ways to add an expense need a Claude API key: Claude turns the
words or the photo into a Draft. Get a key at
[console.anthropic.com](https://console.anthropic.com/) (paid by usage) and set it in `.env`:

```bash
ANTHROPIC_API_KEY=sk-ant-...
BASE_CURRENCY=EUR   # used when the words or the receipt name no currency
```

Without the key, manual entry by category and presets still work. A voice
message is still turned into text, but the bot only shows the text and says it
could not parse the expense.

Voice is turned into text on the server by **Vosk** (offline, free). It needs
the Russian speech model `vosk-model-small-ru-0.22` (about 46 MB download,
about 90 MB unpacked) in `models/` (gitignored):

- **Locally** the bot downloads it on the first voice message, so that reply
  takes longer. To download it in advance:

  ```bash
  python -c "from family_bot.voice_input import ensure_model; ensure_model()"
  ```

- **On Render** the Build Command downloads it (step 6).
- To use another model, set `VOSK_MODEL_PATH` to its folder. Do not set it
  on Render: when it is set, the Build Command skips the download.

### 6. Deploy (optional, e.g. Render)

On [Render](https://render.com/): **New → Web Service** → connect this repo
(or your fork). Then fill in the fields below.

**Build Command** (installs the package and downloads the Vosk speech model
into `models/`, so the first voice message does not wait for it):

```bash
pip install -r requirements.txt && pip install --no-deps -e . && python -c "from family_bot.voice_input import ensure_model; ensure_model()"
```

Keep `-e` (editable install): `.env`, `models/` and a relative
`GOOGLE_CREDS_PATH` resolve from the repo checkout, not from `site-packages`.

**Start Command:**

```bash
family-bot
```

**Python version (required, before the first deploy):** add the env var
`PYTHON_VERSION` = `3.12.12` (row in the table below). Render may not read
`.python-version`, for example on a service created earlier, and builds with
an older Python. Then `pip install --no-deps -e .` fails with
`Package 'family-bot' requires a different Python`. The env var always wins.

**Secret File** (the Google key): service → **Environment → Secret Files →
Add Secret File** → file name `creds.json` → paste the whole contents of your
local `secrets/creds.json` → **Save**. Render puts it at `/etc/secrets/creds.json`.

**Environment Variables** (service → **Environment**):

| Variable | Value |
|----------|-------|
| `PYTHON_VERSION` | `3.12.12` |
| `BOT_TOKEN` | token from @BotFather |
| `GOOGLE_CREDS_PATH` | `/etc/secrets/creds.json` |
| `SHEET_NAME` | exact spreadsheet title, as in `.env` |
| `ALLOWED_USERS` | same line as in `.env` |
| `ANTHROPIC_API_KEY` | optional, for voice / typed text / photos |
| `BASE_CURRENCY` | optional, ISO code of the currency used when an expense names none (default `EUR`), as in `.env` |
| `TZ_NAME` | optional, the family's IANA time zone (default `Europe/Belgrade`). Render runs in UTC; the bot takes "today" and "yesterday" in this zone |
| `WEBHOOK_SECRET` | recommended: a random string of 1–256 characters `A–Z a–z 0–9 _ -` (command below) |

Render sets `RENDER_EXTERNAL_URL` (the service address) by itself. When it is
set, the bot runs in webhook mode, and the log says `Running in webhook mode`.
Telegram posts updates to `<RENDER_EXTERNAL_URL>/telegram`; the bot token is
not in the address.
If the log says `polling mode`, add the variable `RENDER_EXTERNAL_URL` with
the service address `https://<name>.onrender.com`.
Do not put `RENDER_EXTERNAL_URL` in your local `.env`: without it the bot uses polling. Stop the local bot once the
Render one runs — two copies with one token interfere.

Make `WEBHOOK_SECRET` on your computer:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Telegram sends it with every update; the bot answers 403 to requests without
it. If it is unset, the bot makes a random secret at each start and registers
it with Telegram, so it still works; a fixed value avoids rejected updates
while an old and a new instance overlap during a deploy.

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

The bot fills Telegram's command menu (the «/» button) with these commands at
startup (`bot.COMMANDS`), except `/stop`. The bot speaks Russian.

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
