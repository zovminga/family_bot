# Architecture

Deep-module layout for the Telegram bot and its Sheets seam. Dashboard details
live in [`dashboard/SPEC.md`](../dashboard/SPEC.md) — this file only names the
boundary.

## System shape

```
Telegram user
    │
    ▼
bot.py  (handlers, ConversationHandler, allowlist gate, webhook|polling)
    │
    ├── config.py               Config (Allowlist, Sheets, categories, Presets) — post_init
    ├── auth.py                 allowlist id → Spender name
    ├── navigation.py           go_home
    ├── flow_preset.py          ⚡ preset steps
    ├── flows/stats.py          statistics steps, StatQuery
    ├── draft_expense.py        Draft review UI state machine
    ├── voice_input.py          OGG → Vosk → Claude → draft items
    ├── photo_input.py          image → Claude Vision → draft items
    ├── regular.py              Regular_expenses → preset dicts
    ├── analytics.py            stats on normalized rows
    ├── expense.py              ExpenseRecord → row
    ├── amount_input.py         typed/spoken amount checks
    ├── sheet_data.py           parse/normalize kernel
    ├── clock.py                "now" in the family time zone (TZ_NAME)
    ├── errors.py               error handler, user texts, secret masking
    └── sheets.py  SheetsClient ──────────────────────────┐
                                                          ▼
                                              Google Spreadsheet
                                              Data | Config | Regular_expenses | Meta
                                                          ▲
dashboard/build_dashboard.py  (laptop) ───────────────────┘
         → standalone HTML (gitignored output/)
```

Isolation model: one deploy = one bot token = one spreadsheet = one allowlist.
Finances never share across installs.

## Module seams (keep deep)

| Module | Owns | Callers must not |
|--------|------|------------------|
| `sheets.py` | Auth to gspread, worksheet open, domain reads/writes; turns gspread's missing-spreadsheet error into `sheets.SpreadsheetNotFound` | Call gspread directly |
| `expense.py` | `ExpenseRecord`, row layout by Data headers, month derivation | Hand-build Data rows |
| `currency.py` | Currency registry: stored symbol, ISO code, Russian words/stems. Feeds `Config.currencies` (the buttons), voice/receipt prompts and snapping, preset columns, `sheet_data` symbol ↔ ISO maps | List currencies anywhere else; adding one is one line in `CURRENCIES` |
| `amount_input.py` | Rules for a new amount: finite and above zero (`positive_amount`), optional Currency before or after it, at most two decimals (`parse_amount_text`: «1 200», «350 дин», «€12,5»; «1.234» is rejected, not rounded). Number parsing is `sheet_data.parse_amount`, Currency lookup is `currency.find` | Parse typed or spoken amounts another way |
| `clock.py` | `clock.now()`: current time in the family zone (`TZ_NAME`, default `Europe/Belgrade`) | Call `datetime.now()` for "today" |
| `errors.py` | Application error handler `on_error`, user texts `SAVE_FAILED` / `NOT_PARSED`, `log_exception` with `BOT_TOKEN` / API keys masked | Send exception text to the user; log a traceback without `mask_secrets` |
| `sheet_data.py` | Header aliases, amount parse, `DATE_FMT`/`MONTH_FMT`, currency maps derived from `currency.py` | Duplicate parse rules (known gap below) |
| `draft_expense.py` | Draft session, card text, `vx:` callback outcomes | Re-implement confirm UI |
| `analytics.py` | Aggregations + FX adapter hook; also holds the HTTP adapter `http_exchange_rate` | Mix Sheets I/O into stats |
| `config.py` | `Config`: Allowlist, `SheetsClient` (or `None` when Google is not set up), categories, Presets, Currency buttons, setup `problems`. `load(env)` runs in `bot.post_init` and stores it in `bot_data["config"]`; handlers read it with `bot.cfg(context)`. Each setup mistake (Google key, `SHEET_NAME`, `ALLOWED_USERS`, `TZ_NAME`) is one line «what — what to do», never a traceback. `reload` backs `/reloadcats` and its problem lines go into the reply. `load_env_file` reads `.env` (called from `bot.main()`) | Read settings or open Sheets at import time; keep settings in module globals |
| `auth.py` | `ALLOWED_USERS` parse and checks. Pairs split only before the next `<id>:`, so a name may hold a comma (`111:Иван, Петров`) | Scatter allowlist string parsing |
| `navigation.py` | «return home» labels/callbacks | Fork home behaviour per flow |
| `flows/stats.py` | Statistics conversation: `StatsFlow` handlers and their `states()`, request state in one `StatQuery` (`user_data["stat_query"]`); data sources injected from `bot.py`, each called with the handler's `context` | Keep stats parameters as loose `user_data` keys; add stats handlers to `bot.py` |
| `bot.py` | Telegram I/O adapters + wiring | Grow new domain logic here |

Prefer extracting a library module over adding another hundred lines to `bot.py`.

**Known gap until issue #25 closes (finding C3, plan 2.3):** `dashboard/build_dashboard.py`
imports `resolve_columns`, `parse_amount`, `normalize_month` and the currency
maps from `sheet_data`, but runs its own row loop: its own `cell()`, category
repair, symbol → ISO fallback, and its own FX fetch (`fetch_rates`). It does not
use `sheet_data.normalize_rows`.

## Conversation flows

One `ConversationHandler` (`bot.py` `build_application()`), states in `conversation_states.py`.

```
/start → CHOOSE_ACTION (main menu)
           ├─ manual add → CHOOSE_CAT → TYPING_AMT → CHOOSE_CUR
           │                 → TYPING_CMNT → CHOOSE_DT [| TYPING_DT] → save → home
           ├─ preset ⚡   → CHOOSE_PRESET → append_expense → home
           └─ stats      → STAT_CAT → STAT_TYPE → (date|month path)
                             → STAT_GROUP_CURRENCY → STAT_CONVERT_CURRENCY
                             → result → [STAT_SHOW_DETAILS] → CHOOSE_ACTION
```

Stats (`flows/stats.py`): the menu tap and the category step each start a new
`StatQuery`, so a previous request's month, dates or conversion currency never
carry over. Every exit returns `CHOOSE_ACTION` and drops the query; `/start`
drops it too. `STAT_SHOW_DETAILS` is entered only when there are conversion
details to show. `bot.py` builds one `StatsFlow` (`bot.STATS`) and merges
`STATS.states()` into the `ConversationHandler`.

**Outside** the conversation (registered earlier, same group):

- Voice / audio → `voice_handler` → Draft card (`vx:` callbacks)
- Photo / image doc → `photo_handler` → same Draft card
- Allowlist: `TypeHandler` in group `-1` (`enforce_allowlist`); `/whoami` exempt.
  When Google Sheets is not set up (`Config.sheets is None`) an allowed User
  gets the setup problems instead of the flow; `/whoami` still answers.

Startup: importing `bot` reads no `.env` and opens no spreadsheet. `main()`
reads `.env` and stops with one line if `BOT_TOKEN` is missing;
`post_init` loads `Config` and prints each setup problem as one line.

Fallbacks: `/cancel`, `/stop`, `/start`. `allow_reentry=True`.
Home: `navigation.go_home` → `CHOOSE_ACTION`. Clears flow keys in `user_data`, keeps open Drafts.

## Sheet schema

Created/filled by `scripts/bootstrap_sheet.py`:

| Worksheet | Role |
|-----------|------|
| `Data` | Expenses. Columns: Date, Month, Category, Amount, Currency, Who, Comment |
| `Config` | Category list (column A, header + names) |
| `Regular_expenses` | Human preset table (monthly / one-off sections) → `regular.parse_regular` |
| `Meta` | Dashboard Telegram `file_id` for `/dashboard` |

Date format: `DD.MM.YYYY` (`sheet_data.DATE_FMT`). Month key: derived on write.
Writes lay a row out by the Data header names (`resolve_columns` aliases), so
column order in the sheet is free; a sheet without a Date, Category, Amount,
Currency, Who or Comment column rejects the write. An empty header row falls
back to the default order above. Each append first reads the header row
(one extra Sheets call per row). Values are written `RAW`: text such as `=…`
or `2026-11` stays text, amounts are numbers. Date `DD.MM.YYYY` is stored as
text too, not as a Sheets date value; Python readers use `get_all_values` and
are unaffected, sheet-side date formulas and sorting see text.

## Input pipelines

**Manual** (`bot.py`): Category → amount → Currency → comment → date. Category must match `Config.categories` (case ignored); amount goes through `amount_input.parse_amount_text`, and a Currency typed with it skips the Currency step; Currency must be on the buttons (`Config.currencies`), found by symbol, ISO code or word. Anything else re-asks with the list. Edited Telegram messages are stopped in handler group -2 (`bot.EDITED_MESSAGES`) before the allowlist and every flow.

**Voice** (`voice_input.py`): OGG/Opus → PyAV PCM → Vosk (RU model under `models/`, gitignored) → Claude structured expenses constrained to sheet categories/currencies → `draft_expense.session_from_voice`.

**Photo** (`photo_input.py`): image bytes → Claude Vision line items + semantic buckets → Python grouping/sums → `session_from_receipt`. No fiscal QR / zbar (Render-friendly).

**Draft save:** `draft_expense.save_drafts` → `expense.append_expenses` → one
`SheetsClient.append_data_rows` call (gspread `append_rows`, `RAW`): all items
land or none do, so a retry after a Sheets error writes no duplicates.

**Drafts in memory:** `user_data["drafts"]` maps a 4-hex-char Draft id to a
`DraftSession`; several cards stay usable at once. Callback data is
`vx:<id>:<action>[:<item>[:<arg>]]` (`draft_expense.parse_callback`). The
category button carries the category name (`setcat:<i>:<name>`); a name that
would push callback data past Telegram's 64 bytes is carried as an 8-char
hash of the name (`setcathash:<i>:<hash>`), matched against current categories. Save or cancel marks the Draft `closed`; later taps on
that card do nothing, so a double tap keeps «✅ Сохранено». Up to 20 closed
Drafts are kept per user, open ones are never pruned. An unknown id (card from
before a restart) gets «Черновик уже не активен.». Drafts are not persisted:
a restart drops them.

**Errors:** `bot.build_application()` registers `errors.on_error` with
`add_error_handler`. An exception a handler does not catch is logged (traceback,
tokens and keys masked) and the user gets «❌ Не сохранилось. Попробуйте ещё раз».
Voice and photo catch their own errors: a failed Vosk or Claude call shows
«❌ Не разобрал, введите вручную», a failed download asks to try again. No
handler sends exception text to the user.

Voice/photo need `ANTHROPIC_API_KEY`. Without it, manual + presets still work.
Default Claude API id is `claude-sonnet-5` (real, legacy as of 2026-11-18; see `docs/STATUS.md`). Override with `VX_LLM_MODEL` / `PX_LLM_MODEL`. Dashboard grouping uses the same default via `--model`.

## Runtime modes

| Mode | When | How |
|------|------|-----|
| Polling | `RENDER_EXTERNAL_URL` unset | `app.run_polling()` — local default |
| Webhook | `RENDER_EXTERNAL_URL` set | `run_webhook` on `PORT` (default 8443), path = bot token |

## Dashboard boundary

Bot does **not** build the HTML. Laptop runs `dashboard/build_dashboard.py` (or `run.sh`), optionally publishes file + stores `file_id` in Meta; bot only re-sends. A failed publishing build sends a short alert to the same chat (`dashboard/failure_alert.py`). Full pipeline/formulas: `dashboard/SPEC.md`.

## Tests

`pytest` from repo root. `tests/` covers pure modules with fakes — no live Telegram or Sheets. Prefer extending those over manual-only verification for seam changes.

`tests/conftest.py` keeps tests away from `.env` and live Sheets, and holds
`FakeSheetsClient` (in-memory `SheetsClient`). Known-bug reproductions and
their own fakes live in `tests/known_bugs/`.

CI (`.github/workflows/ci.yml`, GitHub Actions) runs on every pull request and
push to `main`: `uv sync --locked` on Python 3.12, then `pytest`, `ruff check`,
`mypy` over the library modules, and `pip-audit`. Rule sets and per-module
relaxations live in `pyproject.toml` under `[tool.ruff]` and `[tool.mypy]`.

## Conventions

- Domain vocabulary: [`CONTEXT.md`](../CONTEXT.md)
- Public install/run: [`README.md`](../README.md)
- Env contract: [`.env.example`](../.env.example)
- Handoff state: [`STATUS.md`](STATUS.md)
