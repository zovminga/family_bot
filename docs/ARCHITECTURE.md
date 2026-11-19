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
    ├── auth.py                 allowlist id → Spender name
    ├── navigation.py           go_home
    ├── flow_preset.py          ⚡ preset steps
    ├── draft_expense.py        Draft review UI state machine
    ├── voice_input.py          OGG → Vosk → Claude → draft items
    ├── photo_input.py          image → Claude Vision → draft items
    ├── regular.py              Regular_expenses → preset dicts
    ├── analytics.py            stats on normalized rows
    ├── expense.py              ExpenseRecord → row
    ├── sheet_data.py           parse/normalize kernel
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
| `sheets.py` | Auth to gspread, worksheet open, domain reads/writes | Call gspread directly |
| `expense.py` | `ExpenseRecord`, row layout by Data headers, month derivation | Hand-build Data rows |
| `sheet_data.py` | Header aliases, amount parse, `DATE_FMT`/`MONTH_FMT`, currency maps | Duplicate parse rules (known gap below) |
| `draft_expense.py` | Draft session, card text, `vx:` callback outcomes | Re-implement confirm UI |
| `analytics.py` | Aggregations + FX adapter hook; also holds the HTTP adapter `http_exchange_rate` | Mix Sheets I/O into stats |
| `auth.py` | `ALLOWED_USERS` parse and checks | Scatter allowlist string parsing |
| `navigation.py` | «return home» labels/callbacks | Fork home behaviour per flow |
| `bot.py` | Telegram I/O adapters + wiring | Grow new domain logic here |

Prefer extracting a library module over adding another hundred lines to `bot.py`.

**Known gap until issue #25 closes (finding C3, plan 2.3):** `dashboard/build_dashboard.py`
imports `resolve_columns`, `parse_amount`, `normalize_month` and the currency
maps from `sheet_data`, but runs its own row loop: its own `cell()`, category
repair, symbol → ISO fallback, and its own FX fetch (`fetch_rates`). It does not
use `sheet_data.normalize_rows`.

## Conversation flows

One `ConversationHandler` (`bot.py` `main()`), states in `conversation_states.py`.

```
/start → CHOOSE_ACTION (main menu)
           ├─ manual add → CHOOSE_CAT → TYPING_AMT → CHOOSE_CUR
           │                 → TYPING_CMNT → CHOOSE_DT [| TYPING_DT] → save → home
           ├─ preset ⚡   → CHOOSE_PRESET → append_expense → home
           └─ stats      → STAT_CAT → STAT_TYPE → (date|month path)
                             → STAT_GROUP_CURRENCY → STAT_CONVERT_CURRENCY
                             → STAT_SHOW_DETAILS → result → home (not reliable, see below)
```

Until issue #16 (plan 1.5, findings R7, R8) closes, the stats branch does not
return home cleanly. `stat_*` keys survive into the next query and past `/start`.
When `show_statistics_result` is called with `ask_details=True` but has no
conversion details, it shows the main menu while the handler still returns
`STAT_SHOW_DETAILS`, so the first menu tap is lost.

**Outside** the conversation (registered earlier, same group):

- Voice / audio → `voice_handler` → Draft card (`vx:` callbacks)
- Photo / image doc → `photo_handler` → same Draft card
- Allowlist: `TypeHandler` in group `-1` (`enforce_allowlist`); `/whoami` exempt

Fallbacks: `/cancel`, `/stop`, `/start`. `allow_reentry=True`.
Home: `navigation.go_home` → `CHOOSE_ACTION`.

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
back to the default order above. Values are written `RAW`: text such as
`=…` or `2026-11` stays text, amounts are numbers.

## Input pipelines

**Voice** (`voice_input.py`): OGG/Opus → PyAV PCM → Vosk (RU model under `models/`, gitignored) → Claude structured expenses constrained to sheet categories/currencies → `draft_expense.session_from_voice`.

**Photo** (`photo_input.py`): image bytes → Claude Vision line items + semantic buckets → Python grouping/sums → `session_from_receipt`. No fiscal QR / zbar (Render-friendly).

**Draft save:** `draft_expense.save_drafts` → `append_expense` per item.

Voice/photo need `ANTHROPIC_API_KEY`. Without it, manual + presets still work.
Default Claude API id is `claude-sonnet-5` (real, legacy as of 2026-11-18; see `docs/STATUS.md`). Override with `VX_LLM_MODEL` / `PX_LLM_MODEL`. Dashboard grouping uses the same default via `--model`.

## Runtime modes

| Mode | When | How |
|------|------|-----|
| Polling | `RENDER_EXTERNAL_URL` unset | `app.run_polling()` — local default |
| Webhook | `RENDER_EXTERNAL_URL` set | `run_webhook` on `PORT` (default 8443), path = bot token |

## Dashboard boundary

Bot does **not** build the HTML. Laptop runs `dashboard/build_dashboard.py` (or `run.sh`), optionally publishes file + stores `file_id` in Meta; bot only re-sends. Full pipeline/formulas: `dashboard/SPEC.md`.

## Tests

`pytest` from repo root. `tests/` covers pure modules with fakes — no live Telegram or Sheets. Prefer extending those over manual-only verification for seam changes.

## Conventions

- Domain vocabulary: [`CONTEXT.md`](../CONTEXT.md)
- Public install/run: [`README.md`](../README.md)
- Env contract: [`.env.example`](../.env.example)
- Handoff state: [`STATUS.md`](STATUS.md)
