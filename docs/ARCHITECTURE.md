# Architecture

Deep-module layout for the Telegram bot and its Sheets seam. Dashboard details
live in [`dashboard/SPEC.md`](../dashboard/SPEC.md) — this file only names the
boundary.

## System shape

All Python code is the `family_bot/` package; module names below are relative
to it (`bot.py` = `family_bot/bot.py`). Inside the package, modules import each
other as `from family_bot import …`. Entry points: the `family-bot` command
(`[project.scripts]` → `family_bot.bot:main`) and `python -m family_bot.dashboard`.
Paths the code resolves at run time stay relative to the repo root: `.env`,
a relative `GOOGLE_CREDS_PATH`, `models/` (Vosk), and `dashboard/` (template,
`output/`, `vendor/`).

```
Telegram user
    │
    ▼
bot.py  (handlers, ConversationHandler, allowlist gate, webhook|polling)
    │
    ├── config.py               Config (Allowlist, Sheets, categories, Presets) — post_init
    ├── auth.py                 allowlist id → Spender name
    ├── navigation.py           go_home, home fallbacks, button grid → inline keyboard
    ├── look.py                 HTML text helpers (escape, bold, quote, bar…) and button styles (no Telegram)
    ├── flow_preset.py          ⚡ preset steps
    ├── flows/manual.py         manual add steps (ManualFlow)
    ├── flows/stats.py          statistics steps, StatQuery
    ├── draft_expense.py        Draft review UI state machine
    ├── live_card.py            live card: edit one message per step, move it down when buried, delete answers
    ├── undo.py                 «↩️ Отменить» after a save (own last save, 30 min)
    ├── voice_input.py          OGG → Vosk → Claude → draft items
    ├── photo_input.py          image → Claude Vision → draft items
    ├── text_input.py           «кофе 350» → voice_input.extract_expenses → Draft
    ├── regular.py              Regular_expenses → preset dicts
    ├── analytics.py            stats on parsed rows (no I/O)
    ├── fx.py                   exchange rates (bot + dashboard)
    ├── llm.py                  Claude client limits, JSON from replies
    ├── expense.py              ExpenseRecord → row
    ├── amount_input.py         typed/spoken amount checks
    ├── sheet_data.py           Data row parsing → Row (bot + dashboard)
    ├── clock.py                "now" in the family time zone (TZ_NAME)
    ├── monthly_summary.py      JobQueue job: last month's summary on the 1st
    ├── payment_reminder.py     Payment reminder: due monthly Presets, text, buttons (no Telegram)
    ├── payment_reminder_bot.py Payment reminder job on the 5th/20th, buttons, amount reply
    ├── errors.py               error handler, user texts, secret masking
    ├── logs.py                 logging setup for entry points (LOG_LEVEL, masking)
    └── sheets.py  SheetsClient ──────────────────────────┐
                                                          ▼
                                              Google Spreadsheet
                                              Data | Config | Regular_expenses | Meta | Users
                                                          ▲
dashboard/build_dashboard.py  (laptop, python -m family_bot.dashboard) ──┘
         → standalone HTML (gitignored dashboard/output/)
```

Isolation model: one deploy = one bot token = one spreadsheet = one allowlist.
Finances never share across installs.

## Module seams (keep deep)

| Module | Owns | Callers must not |
|--------|------|------------------|
| `sheets.py` | Auth to gspread, worksheet open, domain reads/writes; turns gspread's missing-spreadsheet error into `sheets.SpreadsheetNotFound` | Call gspread directly |
| `expense.py` | `ExpenseRecord`, row layout by Data headers, month derivation | Hand-build Data rows |
| `currency.py` | Currency registry: stored symbol, ISO code, Russian words/stems. Feeds `Config.currencies` (the buttons), voice/receipt prompts and snapping, preset columns, `sheet_data` symbol ↔ ISO maps. `format_amount` is the one way the bot shows an amount: «1 234 дин», «12,50 €» (decimals only when not zero) | List currencies anywhere else; adding one is one line in `CURRENCIES`; format an amount with `:,.2f` in a user text |
| `currency_picker.py` | Choosing a Currency (issue #33, plan 3.6): Recent currencies first («🇷🇸 дин», `currency.label`), «🌍 Другая» pages of every rate-service code (`Catalog`, `fx.currency_codes`, cached 1 h, registry codes when the service fails), `search` by code, symbol or Russian name (Babel CLDR names, all plural forms, no network). Telegram-free: the caller passes `cb` (picker payload `pick:<ISO>` / `page:<n>` → its own callback_data). Recent currencies per User: `load_recent` (cache in `user_data["recent_currencies"]` → Users sheet → three most frequent in Data → `дин`, `€`, `₽`), `remember` (writes Users only when the list changed; a failed write is logged, the Expense still saves). A pick is stored as `currency.stored_value`: legacy symbol, else ISO code (Q8). Blocking: callers use `asyncio.to_thread` | Build Currency buttons another way; read Users directly |
| `amount_input.py` | Rules for a new amount: finite and above zero (`positive_amount`), optional Currency before or after it, at most two decimals (`parse_amount_text`: «1 200», «350 дин», «€12,5»; «1.234» is rejected, not rounded). Number parsing is `sheet_data.parse_amount`, Currency lookup is `currency.find` | Parse typed or spoken amounts another way |
| `clock.py` | `clock.now()`: current time in the family zone (`TZ_NAME`, default `Europe/Belgrade`) | Call `datetime.now()` for "today" |
| `monthly_summary.py` | Job on the 1st of each month at `MONTHLY_SUMMARY_HOUR` (default 9) in `TZ_NAME`: `schedule(job_queue, chat_id, hour, load_rows)` (`JobQueue.run_monthly`) and the callback `send_summary`, which reads rows in a thread and sends `analytics.format_month_summary` for `previous_month(clock.now())` to `DASHBOARD_CHAT_ID`. A failure is logged with `errors.log_exception`. The job lives in memory: if the bot is down at that hour, the month is skipped, not sent late | Send scheduled messages another way; catch up a missed summary at start (nothing records that it was sent) |
| `payment_reminder.py` | Payment reminder logic, no Telegram. `due_payments(presets, rows, month)`: monthly Presets (`remind` not False) with no Data row this month whose comment equals the Preset name (case and outer spaces ignored), each with the month's rows of the same Category (`DuePayment`). `Reminder` (short id, open line indices, chat and message id) lives in `bot_data["payment_reminders"]`; `message_text` (HTML: bold Preset name, «уже есть» rows in a quote), `buttons` (`pr:<id>:<line>:pay` with style `success` / `already` without), `saved_text` (quoted «✅ Сохранено» line after «⚡ Внести»), `prompt_text`, `take` / `put_back` (a line is closed before the write so a second tap cannot save twice), `record` (sheet Category, Currency, Spender; the Preset name as comment; `large=False`). Amount prompts wait in `bot_data["payment_reminder_prompts"]` by (chat id, message id) with the presser's id | Credit a payment by guess (amount or Category match); remember «✅ Уже внесено» across reminders |
| `payment_reminder_bot.py` | Telegram side of the Payment reminder. `schedule` puts `send_reminder` on the 5th and 20th (`JobQueue.run_monthly`, `PAYMENT_REMINDER_HOUR`, default 10, in `TZ_NAME`, `monthly_summary.job_tz`); the job reads Presets and Data fresh in a thread, sends nothing when all are entered, logs a failure. `on_button` (callback `pr:`): «⚡ Внести» with an amount saves at once and replies «✅ Сохранено» with the presser's «↩️ Отменить» (`undo.remember`); without an amount it sends a `ForceReply` prompt (in a group with privacy mode the bot sees only replies to its messages); «✅ Уже внесено» removes the line and writes nothing. `AMOUNT_REPLY` (group -1, before the Draft amount text) takes the presser's reply, checks it with `amount_input`, asks again on a bad amount. A button or reply after a restart answers «Напоминание устарело после обновления бота». Like the summary, a reminder missed while the bot is down is skipped | Send the reminder from another place; read Presets from `Config` at send time (the sheet may have changed without `/reloadcats`) |
| `errors.py` | Application error handler `on_error`, user texts `SAVE_FAILED` / `NOT_PARSED`, `log_exception` with `BOT_TOKEN` / API keys masked | Send exception text to the user; log a traceback without `mask_secrets` |
| `logs.py` | `setup()`: one stderr handler on the root logger, level from `LOG_LEVEL` (default INFO; an unknown value → INFO plus one warning line), format `time LEVEL module: message`, every formatted line (traceback too) passed through `errors.mask_secrets`; `httpx` held at WARNING (it logs Telegram URLs with the token). Called once by each entry point after `.env` is read: `bot.main`, `build_dashboard.main`, `failure_alert.main`, `scripts/`. Standard library only (runs under system python3 from `run.sh`) | `print` diagnostics (ruff `T20`; only `scripts/` print their report); call `logging.basicConfig` elsewhere |
| `sheet_data.py` | Header aliases, amount and date parse, `DATE_FMT`/`MONTH_FMT`, currency maps derived from `currency.py`; `parse_rows` turns Data values into `Row` (frozen dataclass: `date` is `datetime.date` or None, `amount` is `float`, the rest stored text). Bot stats and the dashboard build both read rows through it | Read Data cells by index or parse amounts/dates another way |
| `draft_expense.py` | Draft session, card text, `vx:` callback outcomes; item key `large` (Large expense flag: `mark_large` on creation, `large:<i>` toggle, written by `save_drafts`) | Re-implement confirm UI |
| `live_card.py` | Live card (Telegram adapter). `show(card, text, markup, markup_only=)` edits the card, or in a private chat where `TrackingBot.sent_log` holds a newer bot message, sends it again at the bottom and deletes the old one; returns the card to keep. `drop_reply(message)` deletes the person's answer in a private chat. `remove(card)` deletes a card left without a result; conversation flows keep their card in `user_data[KEY_CARD]` and `navigation.leave_flow` removes it. `TrackingBot` (the Application's bot) records every `send*` result and every `delete_message` per chat (last 20 ids); edits do not count. «Message is not modified» on an edit is ignored | Delete messages in group chats; track the last message by hand in a flow |
| `undo.py` | User's last save in `user_data["last_save"]`, «↩️ Отменить» button (`undo:<id>`), ownership and 30-minute checks; rows deleted by `expense.delete_expenses` | Delete «the last row of Data» |
| `analytics.py` | Filters and totals over `list[Row]`; no I/O. `filter_rows`, `last_n`, `format_last_n`, `format_period_stats` take `large=False` (Large expenses left out) or `large=True` (only Large expenses; nothing → «🔶 Крупных трат за этот период нет» / «📭 Крупных трат нет»). Conversion takes a `get_rate(from, to)` function from the caller; a missing rate gives «❌ Нет курса для пересчёта в X: A, B…» with every Currency that has none, never a partial sum. `month_summary(rows, month)` → `MonthSummary` (per Currency: total, Categories, Spenders — Large expenses left out — plus `large`, that month's Large expense rows) and `format_month_summary` for any `YYYY-MM` (HTML, look B: bold total and every Category with a `look.bar` share per Currency; Spenders and «🔶 Крупные (не в итоге)» in one `look.expandable_quote`; «Трат нет» for an empty month). `format_period_stats(rows, header, …)` builds a statistics answer for a month or a period in the same look (Large expenses of the period in the collapsed block unless they are the subject; with `convert_to` one block in that Currency and «💱 Курс» lines in the collapsed block; `convert_rows` does the conversion). `format_last_n` lines: «<b>Кофе — 350 дин</b> · Еда · 23.11 · Аня» under «📜 <b>Последние записи</b>» (🔶 header for Large expenses) |
| `large_expense.py` | Large expense decision: `suggest(amount, currency, threshold=, base=, get_rate=)` — above the threshold in the base Currency; no rate → False. `THRESHOLD_ENV`, `DEFAULT_THRESHOLD` | Compare an amount with the threshold elsewhere | Mix Sheets or HTTP into stats |
| `fx.py` | Exchange rates from exchangerate-api.com: `fetch_rates(base_iso)` (whole table, dashboard) and `rate(from, to)` (one pair, `None` on failure; bot stats via `StatsFlow(get_rate=fx.rate)`). `rate` reads the table of the target Currency from `RATES` (`RateCache`): every source Currency of one stats request shares one call, the table is kept 1 h (`RATE_TTL_SEC`), a failed read is not repeated for 60 s. Tests replace `fx.RATES` (autouse fixture: no network). Blocking: the bot calls it in `asyncio.to_thread` | Call the rates API elsewhere |
| `llm.py` | `client()`: Claude client with the bot limits `LLM_TIMEOUT_SEC` / `LLM_MAX_RETRIES`; `reply_text(resp)` and `extract_json_object(text)` for model replies, in the bot and the dashboard | Parse JSON out of a model reply by hand |
| `config.py` | `Config`: Allowlist, `SheetsClient` (or `None` when Google is not set up), categories, Presets, Currency buttons, `default_currency` (stored symbol of `BASE_CURRENCY`, written when an expense names no Currency; unset → EUR), `summary_chat_id` (`DASHBOARD_CHAT_ID`, `None` when unset), `summary_hour` (`MONTHLY_SUMMARY_HOUR`) and `reminder_hour` (`PAYMENT_REMINDER_HOUR`, default 10), `large_threshold` and `large_threshold_currency` (`LARGE_EXPENSE_THRESHOLD`, «1000 EUR»; a bare number and the default are €, not the base Currency), setup `problems`. `load(env)` runs in `bot.post_init` and stores it in `bot_data["config"]`; handlers read it with `bot.cfg(context)`. Each setup mistake (Google key, `SHEET_NAME`, `ALLOWED_USERS`, `TZ_NAME`, `BASE_CURRENCY`, `DASHBOARD_CHAT_ID`, `MONTHLY_SUMMARY_HOUR`, `PAYMENT_REMINDER_HOUR`, `LARGE_EXPENSE_THRESHOLD`) is one line «what — what to do», never a traceback. `reload` backs `/reloadcats` and its problem lines go into the reply. `load_env_file` reads `.env` (called from `bot.main()`) | Read settings or open Sheets at import time; keep settings in module globals |
| `auth.py` | `ALLOWED_USERS` parse and checks. Pairs split only before the next `<id>:`, so a name may hold a comma (`111:Иван, Петров`) | Scatter allowlist string parsing |
| `look.py` | Bot look, no Telegram. Every message is Telegram HTML (`Defaults(parse_mode=HTML)` in `bot.build_application`), so human text reaches a message only through `escape` (`<`, `>`, `&`; quotes stay). Leaf helpers take plain text and escape it: `bold`, `strike`, `code`, `field` («label: <b>value</b>»); `quote` / `expandable_quote` take HTML. `bar(share)` — 10-cell share bar; `plural` / `expenses` — «1 трата / 3 траты / 5 трат». `Button` = (label, callback_data[, style]), `ButtonGrid`, styles `SUCCESS` / `DANGER` | Put user text into a message unescaped; build HTML tags by hand where a helper exists |
| `navigation.py` | Main-menu labels (`ADD_EXPENSE`, `ADD_PRESET`, `THIS_MONTH`, `SHOW_STATS`, `DASHBOARD`, matched by `bot.choose_action`), «return home» labels/callbacks (old English labels still accepted); `home_fallbacks()` (the only home handler, in the `ConversationHandler` `fallbacks`); `INPUT_TEXT` (text a state reads: not a command, not home); `inline_markup(grid, home=)` — the one adapter from a `look.ButtonGrid` to `InlineKeyboardMarkup`, style → the button's `style` field (Bot API 9.4, python-telegram-bot ≥ 22.7) | Check the home button inside a state handler; build `InlineKeyboardButton` rows from a grid by hand |
| `flows/manual.py` | Manual add conversation on one live card: `ManualFlow` handlers and their `states()`, `card_text`; step values in `user_data` (`amt`, `cur`, `spender`, `large`, `cat`, `comment`, the card under `live_card.KEY_CARD`); data sources injected from `bot.py` (threshold, base Currency and `fx.rate` for the Large expense step) | Add manual-input handlers to `bot.py` |
| `flows/stats.py` | Statistics conversation: `StatsFlow` handlers and their `states()`; `this_month` answers «📅 Этот месяц» from the menu without a conversation state; request state in one `StatQuery` (`user_data["stat_query"]`) and the questions message in `user_data["stat_card"]`; data sources injected from `bot.py`, each called with the handler's `context`. Answers: `analytics.format_period_stats` (month / period, month-summary look) and `analytics.format_last_n` | Keep stats parameters as loose `user_data` keys; add stats handlers to `bot.py` |
| `per_user.py` | `PerUserUpdateProcessor` (python-telegram-bot `concurrent_updates`): updates of different users run together, one user's updates run in order | Turn on plain `concurrent_updates(True)`: a Draft double tap would save twice |
| `bot.py` | Telegram I/O adapters + wiring. Blocking calls (Sheets, `config.load` / `reload`, Vosk, Claude) go through `asyncio.to_thread`; `flows/stats.py` and `flow_preset.py` do the same for rows, FX and preset saves | Call Sheets or FX directly in a handler: it freezes the event loop for everyone |

Prefer extracting a library module over adding another hundred lines to `bot.py`.

The dashboard build (`family_bot/dashboard/build_dashboard.py`) reads rows with
`sheet_data.parse_rows`, rates with `fx.fetch_rates` and model replies with
`llm.extract_json_object`. Its own steps on top: cash-withdrawal exclusion,
empty Currency → base currency, spender names, `repair_category()`.

## Conversation flows

One `ConversationHandler` (`bot.py` `build_application()`), states in the
`IntEnum` `conversation_states.State`; a test checks every state has a handler.

```
/start → CHOOSE_ACTION (main menu)
           ├─ other text → text_expense → Draft card (stays in CHOOSE_ACTION)
           ├─ 📅 this month → StatsFlow.this_month → summary (stays in CHOOSE_ACTION)
           ├─ manual add → TYPING_AMT → [CHOOSE_CUR, unless typed with the amount]
           │   (one card)    [→ ASK_LARGE, only above the threshold] → CHOOSE_CAT
           │                 → TYPING_CMNT → CHOOSE_DT [| TYPING_DT] → save → CHOOSE_ACTION
           │                   TYPING_CMNT ─ «✅ Сегодня, без комментария» → save
           │                   main-menu button in any of these → card removed → that button
           ├─ preset ⚡   → CHOOSE_PRESET → append_expense → home
           │                 no amount → TYPING_PRESET_AMT (card) → append_expense → home
           └─ stats      → STAT_CAT [all | one Category | 🔶 only Large] → STAT_TYPE → (date|month path)
                             → STAT_GROUP_CURRENCY → STAT_CONVERT_CURRENCY
                             → answer (new message; questions message deleted) → CHOOSE_ACTION
```

Stats (`flows/stats.py`): the menu tap and the category step each start a new
`StatQuery`, so a previous request's month, dates, conversion currency or
«🔶 Только крупные» choice (`StatQuery.only_large`) never carry over. Every
request leaves Large expenses out; `only_large` takes the same steps over Large
expenses only. Every exit returns `CHOOSE_ACTION` and drops the query; `/start`
drops it too. All questions are one bot message (`user_data["stat_card"]`)
that each step edits; the step buttons are inline (callback prefix `st:`),
dates and a Currency name are typed and the typed message is deleted (`live_card.drop_reply`, private
chat only). The answer is a new message, then the questions message is
deleted. The main-menu keyboard stays on screen: `STATS.states(menu=choose_action)`
sends a menu tap (and any typed text on a buttons-only step) to the menu after
deleting the questions. The inline «🏠 В начало» under a question is taken by
the stats state itself (`StatsFlow.on_home`, deletes the questions); the typed
home text still reaches the fallback. The Conversion target (`STAT_CONVERT_CURRENCY`) uses the
Currency picker like manual input (callback prefix `scur:`, typed code or name
too); any Currency of the rate service, and the pick goes to the User's Recent
currencies. `bot.py` builds one `StatsFlow` (`bot.STATS`) and merges
`STATS.states()` into the `ConversationHandler`. Manual add (`flows/manual.py`)
works the same way: `bot.MANUAL` is a `ManualFlow`, `MANUAL.states()` is merged.

**Outside** the conversation (registered earlier, same group):

- Voice / audio → `voice_handler` → Draft card (`vx:` callbacks)
- Photo / image doc → `photo_handler` → same Draft card
- «↩️ Отменить» → `undo_callback` (`undo:` callbacks)
- Edited messages: dropped in group `-3` (`EDITED_MESSAGES`).
- Allowlist: `TypeHandler` in group `-2` (`enforce_allowlist`); `/whoami` exempt.
- Typed amount after «✏️ Сумма» on a Draft card: `draft_amount_text` in group
  `-1`. It acts only while `user_data["draft_amount_edit"]` is set, redraws the
  card and stops the update (`ApplicationHandlerStop`). A command, text with
  no digit (menu button, word), a new Draft card or any card button ends the
  wait, and the text goes on to the conversation.
  When Google Sheets is not set up (`Config.sheets is None`) an allowed User
  gets the setup problems instead of the flow; `/whoami` still answers.

Startup: importing `bot` reads no `.env` and opens no spreadsheet. `main()`
reads `.env` and stops with one line if `BOT_TOKEN` is missing;
`logs.setup()` runs right after `.env` is read, before that check.
`post_init` loads `Config`, logs each setup problem as one WARNING line, schedules
the monthly summary and the Payment reminder when `DASHBOARD_CHAT_ID` is set and Sheets is open
(`monthly_summary.schedule`, `payment_reminder_bot.schedule`)
and fills the Telegram command menu from `bot.COMMANDS` (`set_my_commands`; a Telegram error
there is logged and the bot still starts).

UI language: every text, button and error is Russian (plan 3.1);
`tests/test_russian_ui.py` walks the flows and fails on an English word.
Category and Spender names are shown as in the sheet.

Entry points: `/start`, main-menu buttons (`navigation.MENU_TEXT`, old English
labels included) and the home button. They serve a User with no conversation
state, which is everyone right after a restart (memory is wiped on deploy).
`allow_reentry=False`: inside a flow its states read the text.
Fallbacks: home button, `/cancel`, `/stop`, `/start`.
Home: state handlers never see it — their filters exclude the home text
(`navigation.INPUT_TEXT`) and the `to_start` callback (exception: the stats
states take the `to_start` callback to delete their questions message). `navigation.home_fallbacks()`
catches both in every state and calls `navigation.go_home` → `CHOOSE_ACTION`.
`go_home` = `leave_flow` (clear flow keys of `user_data`, delete the live card
still in `user_data[live_card.KEY_CARD]`) + the main-menu message. A flow that
turned its card into a result pops the key first. Main-menu buttons in the
manual states go to `bot.menu_mid_input` (`leave_flow`, then `choose_action`)
before the state's text handler.
So an old inline home button now works in any state (before #24 only in
`TYPING_CMNT` / `CHOOSE_DT`), and typed home text works in `CHOOSE_DT` too.
Clears flow keys in `user_data`, keeps open Drafts and the last save (undo).

## Sheet schema

Created/filled by `scripts/bootstrap_sheet.py`:

| Worksheet | Role |
|-----------|------|
| `Data` | Expenses. Columns: Date, Month, Category, Amount, Currency, Who, Comment, Large. `Large` (alias «Крупная») is optional: the Large expense mark, written «да» or empty; on read any non-empty value except «нет», «no», «0», «false» marks the row (`sheet_data.is_large_cell`). A sheet without it reads every row as unmarked; the first marked write adds it after the last header (`expense._headers_for` → `SheetsClient.add_data_column`, one lock against two adds) |
| `Config` | Category list (column A, header + names) |
| `Regular_expenses` | Human preset table → `regular.parse_regular`. Columns: name, one column per Currency (header word known to `currency.find`: «динары», «евро», «CNY»), «категория», «кто». Divider rows «ежемесячные траты» / «разовые траты» start the sections. The first Currency column holding a number gives amount and Currency. A row with no number: skipped in «разовые траты»; in «ежемесячные траты» it is a Preset with `amount=None` (utilities), Currency from the first Currency column holding any other mark («?»), else `Config.default_currency`. Optional column «напоминать»: when present each Preset gets `remind` (True only for «да»), and the Payment reminder skips the others; without it Presets have no `remind` key and every monthly one is reminded |
| `Meta` | Dashboard Telegram `file_id` for `/dashboard` |
| `Users` | One row per Telegram account: User ID, Name (Spender), Recent currencies (stored values, newest first, «CNY, дин, €»). The bot creates it on the first Currency choice if bootstrap did not; read/written by `SheetsClient.read_recent_currencies` / `write_recent_currencies` (`RAW`) |

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

**Manual** (`flows/manual.py`, one live card since #103): amount → Currency → [«🔶 Это крупная трата?», `ASK_LARGE`, callback `large:yes` / `large:no`, only when `large_expense.suggest` says so] → Category → comment → date. Every step goes through `ManualFlow._show` → `live_card.show` on the card kept in `user_data[live_card.KEY_CARD]` (a callback uses `query.message`); `card_text` puts the title, the filled fields (`look.field`), the error and the question; the step's buttons plus «🏠 В начало» are inline. Typed answers are deleted (`live_card.drop_reply`) before the card is redrawn; a wrong one shows the reason with the same step. `save_row` turns the card into `undo.saved_text` + «↩️ Отменить», pops the card key and calls `navigation.leave_flow` (no main-menu message). The Large question comes before the comment step, so «✅ Сегодня, без комментария» never skips it; ⚡ Presets always write `large=False`. Category: inline buttons `mcat:<index>` (a stale index asks again) or typed text matching `Config.categories` (case ignored); amount goes through `amount_input.parse_amount_text`, and a Currency typed with it skips the Currency step; on the comment step «✅ Сегодня, без комментария» (`today_no_comment`) saves with today's date and an empty comment, skipping `CHOOSE_DT`; the Currency step is `currency_picker` as inline buttons (callback `cur:pick:<ISO>`, `cur:page:<n>`): Recent currencies of the User, «🌍 Другая» pages, or typed text — one match is chosen, several come back as buttons, none re-asks with Recent currencies. The saved Expense's Currency goes to Recent currencies (`save_row`), so a cancelled entry leaves them as they were. A registry word («доллар», «динар») means the registry Currency; «канадский доллар» finds the others. Edited Telegram messages are stopped in handler group -3 (`bot.EDITED_MESSAGES`) before the allowlist and every flow.

**Preset ⚡** (`flow_preset.py`): the ⚡ button of a Preset with an amount saves at once. A Preset with `amount=None` stores itself in `user_data["preset"]`, sends a live card «⚡ <b>name</b>», Категория, Кто, «💵 Введи сумму (<Currency>):» with the inline home button (`user_data[live_card.KEY_CARD]`) and moves to `TYPING_PRESET_AMT`. The answer is deleted and goes through `amount_input.parse_amount_text` (same checks as manual input; a wrong one puts `AMOUNT_ERROR` on the card); a Currency typed with the amount replaces the sheet's. Saved: today, Category and Spender from the sheet, comment = Preset name; the card (or, for a Preset with an amount, a new message) becomes `undo.saved_text` + «↩️ Отменить», then the main menu is sent (the ⚡ keyboard replaced it).

**Voice** (`voice_input.py`): OGG/Opus → PyAV PCM → Vosk (RU model under `models/`, gitignored) → Claude structured expenses constrained to sheet categories/currencies → `draft_expense.session_from_voice`.

**Text** (`text_input.py`): any text in the main menu (`CHOOSE_ACTION`) that is not a menu button goes to `bot.text_expense`. Text with no digit gets `text_input.HINT` without a Claude call. Otherwise `text_input.draft_from_text` calls `voice_input.extract_expenses` (same prompt, snapping and amount check as voice) → `draft_expense.session_from_text` → the same Draft card, the typed text in its quote. No expense found → `HINT`; a Claude error → `errors.NOT_PARSED`. Text only reaches the handler inside the conversation, so after a bot restart the user needs `/start` or a menu button first.

**Photo** (`photo_input.py`): image bytes → Claude Vision line items + semantic buckets → Python grouping/sums → `session_from_receipt`. No fiscal QR / zbar (Render-friendly).

**Draft save:** `draft_expense.save_drafts` → `expense.append_expenses` → one
`SheetsClient.append_data_rows` call (gspread `append_rows`, `RAW`): all items
land or none do, so a retry after a Sheets error writes no duplicates.

**Undo** (`undo.py`, issue #31): the manual save and the Draft save call
`undo.remember`, which keeps the written `ExpenseRecord`s in
`user_data["last_save"]` and returns the «↩️ Отменить» button. A tap deletes
those rows (`expense.delete_expenses` → one `SheetsClient.delete_data_rows`
call, gspread `batch_update` with `deleteDimension`, bottom-up). Data has no row
id, so the row is found by its values: date, Category, Currency, Spender,
comment equal, amount equal as a number; the bottom-most match wins, each row
once. A row edited or deleted in the sheet since → nothing is deleted. Read
and delete run under one lock (one bot process), so two Users' undos cannot
shift each other's rows; a hand edit in the sheet between them still can. Refused
(alert, button stays): another User's save (their `user_data` holds none), an
earlier save of the same User, a save from before a restart. An own save past
30 minutes gets the alert and loses its button (`UndoResult.drop_button`; no
timer, it goes on the tap). The button is red
(`look.DANGER`). `undo.saved_text` / `undo.undone_text` build «✅ Сохранено» /
«↩️ Отменено» with the records in a quote (bold / struck through; one record:
«what — amount» line, then Category · day.month · Spender; several: one line
each). The Draft, manual and ⚡ Preset saves use `saved_text` (manual adds
«🔶 Крупная трата» below when marked); a done undo replaces any message with
`undone_text` and no buttons. The Payment reminder uses `undo.saved_text` too (#107).

**Draft card look** (issue #101, spec `docs/specs/2026-11-bot-look.md`, variant
B): `card_text` → «📝 <b>Черновик траты</b>» (several items: «📝 <b>Черновик:
3 траты</b>»), then `DraftSession.transcript` in a quote (voice transcript,
typed text, or «Чек · <merchant>» for a receipt), then for one item
«Сумма / Что / Категория / Дата / Кто: <b>value</b>» lines (`look.field`) and
«🔶 Крупная трата»; for several «1. <comment> — <b>amount</b> · Category · date»
lines and «Итого: <b>…</b>» summed per Currency («120 дин + 5 €»). The note
follows as plain text. Emoji only in the title (and the 🔶 mark). Buttons:
«✅ Сохранить» `success`, «❌ Отмена» `danger`, the rest plain; «❌ Отмена» turns
the card into «Черновик отменён».

**Draft card edits:** «✏️ Сумма» (`amt:<i>`) sets
`user_data["draft_amount_edit"]`; the next text goes through
`draft_expense.set_amount` (`amount_input.parse_amount_text`, same checks as the
manual flow; a Currency typed with it replaces the item's). «👤 Кто» (`who:<i>`) lists the Allowlist Spender names;
`setwho:<i>:<index>` carries the index (names may not fit 64 bytes; Drafts and
the Allowlist both live until restart). «💱 Валюта» (`cur:<i>`) shows the
User's Recent currencies, `cur:<i>:page:<n>` a page of all Currencies; both set
`draft_amount_edit` with `currency=True`, so typed text («GEL», «юань») goes
through `draft_expense.search_currency`; text with a digit, a command or a menu
button ends that wait. `cur:<i>:pick:<ISO>` sets the item's Currency; the
choice goes to Recent currencies (`CallbackOutcome.picked`).

**Draft edits run on a live card** (issue #102): edit steps go through
`bot._show_step` → `live_card.show`; `AmountEdit.card` is replaced when the card
moves. Typed amount / Currency answers are deleted with `live_card.drop_reply`
before the card is redrawn; a wrong amount shows `draft_expense.amount_step`
with `AMOUNT_ERROR` (same step, wait kept). Text that ends the wait (menu
button, command, a word) is not deleted. Save and cancel edit the card in place
through the callback query. `voice_handler` deletes the voice after the Draft
card is shown; `photo_handler` keeps the photo.

**Large expense on the Draft card** (issue #86): `bot._show_draft` (voice, text,
photo) calls `draft_expense.mark_large` in a worker thread before the card is
shown: each item gets `large` from `large_expense.suggest` (threshold
`Config.large_threshold` in `Config.large_threshold_currency`, `fx.rate`; no rate →
unmarked). The flag is not recomputed after «✏️ Сумма» or «💱 Валюта».
«🔶 Крупная» (`large:<i>`) flips it for any item, below the threshold too; it
sits next to «✏️ Сумма» / «👤 Кто» on a one-item card and in the item edit of a
multi-item card. The card shows «🔶 Крупная трата» (one item) or
«🔶» before the comment (several). `save_drafts` passes it to
`ExpenseRecord.large` → Data column `Large`. An item without the key is unmarked.

**Drafts in memory:** `user_data["drafts"]` maps a 4-hex-char Draft id to a
`DraftSession`; several cards stay usable at once. Callback data is
`vx:<id>:<action>[:<item>[:<arg>]]` (`draft_expense.parse_callback`). The
category button carries the category name (`setcat:<i>:<name>`); a name that
would push callback data past Telegram's 64 bytes is carried as an 8-char
hash of the name (`setcathash:<i>:<hash>`), matched against current categories. Save or cancel marks the Draft `closed`; later taps on
that card do nothing, so a double tap keeps «✅ Сохранено». Up to 20 closed
Drafts are kept per user, open ones are never pruned. An unknown id (card from
before a restart) gets the alert `draft_expense.STALE_TEXT` «Черновик устарел
после обновления бота, отправьте ещё раз»; the card keeps its text and loses
its buttons. Drafts are not persisted (Q15): a restart drops them.

**Errors:** `bot.build_application()` registers `errors.on_error` with
`add_error_handler`. An exception a handler does not catch is logged (traceback,
tokens and keys masked) and the user gets «❌ Не сохранилось. Попробуй ещё раз».
Voice, text and photo catch their own errors: a failed Vosk or Claude call shows
«❌ Не разобрал. Введи вручную: «💰 Добавить трату»», a failed download asks to try again. No
handler sends exception text to the user.

The bot's Claude clients (`voice_input`, `photo_input`) come from `llm.client()`, with the timeout and retry limits `llm.LLM_TIMEOUT_SEC` / `LLM_MAX_RETRIES`. The dashboard's grouping client keeps the SDK defaults.

Voice/text/photo need `ANTHROPIC_API_KEY`. Without it, manual + presets still work.
Default Claude API id is `claude-sonnet-5` (real, legacy as of 2026-11-18; see `docs/STATUS.md`). Override with `VX_LLM_MODEL` / `PX_LLM_MODEL`. Dashboard grouping uses the same default via `--model`.

## Runtime modes

| Mode | When | How |
|------|------|-----|
| Polling | `RENDER_EXTERNAL_URL` unset | `app.run_polling()` — local default |
| Webhook | `RENDER_EXTERNAL_URL` set | `run_webhook` on `PORT` (default 8443), path `/telegram` (`config.WEBHOOK_PATH`), `secret_token` from `WEBHOOK_SECRET` (random per start when unset; invalid value → one-line exit). python-telegram-bot answers 403 to requests without the `X-Telegram-Bot-Api-Secret-Token` header |

## Dashboard boundary

Bot does **not** build the HTML. Laptop runs `python -m family_bot.dashboard` (`family_bot/dashboard/__main__.py` calls `build_dashboard.main`; usually through `dashboard/run.sh`), optionally publishes file + stores `file_id` in Meta; bot only re-sends. A failed publishing build sends a short alert to the same chat (`family_bot/dashboard/failure_alert.py`). Full pipeline/formulas: `dashboard/SPEC.md`.

## Tests

`pytest` from repo root. `tests/` covers pure modules with fakes — no live Telegram or Sheets. Prefer extending those over manual-only verification for seam changes.

`tests/conftest.py` keeps tests away from `.env` and live Sheets, and holds
`FakeSheetsClient` (in-memory `SheetsClient`). Known-bug reproductions and
their own fakes live in `tests/known_bugs/`. Its `make_update` / `FakeMessage` /
`FakeBot` give each message an id in its chat, record edits and deletions, and
keep the bot's `sent_log` (pass one `bot=` to several updates to share it);
`tests/test_draft_live_card.py` shows the live-card scenarios.

CI (`.github/workflows/ci.yml`, GitHub Actions) runs on every pull request and
push to `main`: `uv sync --locked` on Python 3.12, then `pytest`, `ruff check`,
`mypy` over the library modules, and `pip-audit`. Rule sets and per-module
relaxations live in `pyproject.toml` under `[tool.ruff]` and `[tool.mypy]`.

## Conventions

- Domain vocabulary: [`CONTEXT.md`](../CONTEXT.md)
- Public install/run: [`README.md`](../README.md)
- Env contract: [`.env.example`](../.env.example)
- Handoff state: [`STATUS.md`](STATUS.md)
