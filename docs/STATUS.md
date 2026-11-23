# Project status

> Living handoff. Agents update this at the end of every meaningful task
> (see `AGENTS.md` → Keep docs fresh). Newest facts on top inside each section.

**Last updated:** 2026-11-23  
**Updated by:** agent session (issue #101: Draft card in variant B, saved/undone lines; merged main with #104, #106)  
**Product code tip:** branch for issue #101 (2026-11-23) — Draft card with labelled bold fields, green «✅ Сохранить» / red «❌ Отмена», «✅ Сохранено» / «↩️ Отменено» quote lines; on top of #104 (month summary in look B) and #106 (Payment reminder in look B)  
**Repo:** https://github.com/zovminga/family_bot.git (public)

## Snapshot

Stable Telegram expense bot with Google Sheets backend and a separate offline
HTML dashboard. Core flows work: manual add, presets, stats, voice, receipt
photo, allowlist. Last product/code commit: **2026-08-12**. Local workspace
has a clean tree matching GitHub; no local `.env` in this checkout.

## What works

- Draft card in variant B (issue #101, spec `docs/specs/2026-11-bot-look.md`
  stories 1–3, 6, 8, 10–12, 30–34, 36–37): voice, typed text and receipt
  Drafts show «📝 <b>Черновик траты</b>», the transcript / typed text /
  «Чек · <merchant>» in a quote, then «Сумма: <b>350 дин</b>», Что, Категория,
  Дата, Кто and «🔶 Крупная трата». Several items: «1. Хлеб — <b>120 дин</b> ·
  Еда · 23.11.2026» lines and «Итого» per Currency. «✅ Сохранить» is green
  (`success`), «❌ Отмена» red (`danger`), the others plain; the category
  button is «🏷 Категория». Save turns the card into «✅ Сохранено» with the
  records in a quote (one line per item) and a red «↩️ Отменить» that undoes
  them all; undo (any save) turns the message into «↩️ Отменено» with the
  records struck through and no buttons; a tap after 30 minutes drops the
  button. «❌ Отмена» leaves «Черновик отменён». Code: `draft_expense.card_text`,
  `undo.saved_text` / `undone_text`. Manual and ⚡ Preset saves keep their old
  one-line «✅ Сохранено: …» until #103; the Payment reminder builds the same
  look with its own `payment_reminder.saved_text` (#106); editing
  inside the card (#102) is not done. Not checked in a real Telegram chat yet.
- Month summary in look B (issue #104, spec `docs/specs/2026-11-bot-look.md`,
  stories 45–51): `analytics.format_month_summary` writes «📅 <b>Итог за
  ноябрь 2026</b>», then one block per Currency: «💰 Итого <b>сумма</b>» and
  every Category (not only the top 3) as «<b>Еда</b> 48 300» with a 10-cell
  bar and the percent of that Currency's total. Currencies are never added.
  «👤 По людям» (one line per Spender, amounts in several Currencies joined by
  « · ») and «🔶 Крупные (не в итоге)» (one line per Large expense) sit in one
  `<blockquote expandable>`. A month without expenses: header and «Трат нет».
  «📅 Этот месяц» and the 1st-of-month post in `DASHBOARD_CHAT_ID` share this
  text; statistics answers keep the old look until #105. Not checked in a real
  Telegram yet.
- Payment reminder in look B (issue #106, spec `docs/specs/2026-11-bot-look.md`
  stories 52–55): title in bold; each payment is a block «<b>Preset</b> — 850 € ·
  Category», the month's rows of that Category below it in a quote («уже есть:
  дата — сумма — комментарий»); blocks split by an empty line; human text
  escaped. «⚡ Внести: <name>» is `success` (green), «✅ Уже внесено» has no
  style. The reply after «⚡ Внести» is «✅ Сохранено» with
  «<b>Аренда — 850 €</b> / Жильё · 20.11 · Оба» in a quote
  (`payment_reminder.saved_text`, local to the reminder). Follow-up after #101:
  build it with #101's saved-line helper instead. The amount prompt says
  «Ответь на это сообщение суммой.» (was «Ответьте»). The bot deletes nothing in
  the group chat. Not checked in a real Telegram chat yet.
- HTML in every message (issue #100, spec `docs/specs/2026-11-bot-look.md`,
  first of #100–#108): the Application sends every message with parse mode
  HTML (`Defaults(parse_mode=HTML)` in `bot.build_application`). Human text
  (comment, Category, receipt item, merchant, voice transcript, Preset name,
  Spender, Currency, setup problem lines) goes through `look.escape`, so
  «M&M's <3» shows as typed. «нет доступа» shows the Telegram ID in
  `<code>` (the old `parse_mode="Markdown"` is gone). New Telegram-free module
  `look.py`: `escape`, `bold`, `strike`, `code`, `quote`, `expandable_quote`,
  `field` («Сумма: <b>350 дин</b>»), `bar` (10 cells, rounded half up: 41% →
  4 cells, 4% → none), `plural` / `expenses` («1 трата», «3 траты», «5 трат»;
  the multi-item Draft card now says «3 траты» instead of «трат(ы)»). Button
  grids take an optional style, `look.SUCCESS` / `look.DANGER`, which
  `navigation.inline_markup` (the one grid → inline keyboard adapter) puts in
  Telegram's `style` field (#101 colours the Draft and undo buttons).
  python-telegram-bot lower bound is 22.7. Other texts look as before; the
  redesign is #101–#108. Tests: `tests/test_look.py`. Not checked in a real
  Telegram chat yet.
- Payment reminder (issue #89, spec
  `docs/specs/2026-11-large-receipt-reminders.md`): on the 5th and 20th at
  `PAYMENT_REMINDER_HOUR` (default 10) in `TZ_NAME` the bot sends to
  `DASHBOARD_CHAT_ID` the monthly Presets with no Data row this month whose
  comment equals the Preset name (case and outer spaces ignored). Under each:
  the month's Data rows of the same Category («уже есть: дата — сумма —
  комментарий»), so a person decides; nothing is credited by guess. All
  entered → no message. No chat or no Sheets → no job. Buttons per payment:
  «⚡ Внести: <name>» saves at once (sheet Category, Currency, Spender; Preset
  name as comment; today; no Large mark) and replies «✅ Сохранено» with
  «↩️ Отменить» for the presser; a Preset without an amount asks it by
  `ForceReply` (works in a group with privacy mode), only the presser's reply
  counts, same checks as manual input. «✅ Уже внесено» removes the line and
  writes nothing. Optional Regular_expenses column «напоминать»: present →
  only rows with «да». The reminder lives in `bot_data`; after a restart its
  buttons answer «Напоминание устарело после обновления бота», and a reminder
  whose hour passes while the bot is down is skipped. Code:
  `payment_reminder.py` (no Telegram), `payment_reminder_bot.py`. Tests:
  `tests/test_payment_reminder.py`. Not checked in a real Telegram group yet.
- Large expense on the dashboard (issue #88, spec stories 15–16): rows marked
  in the Data column `Large` go to `DATA.large`, not `DATA.rows`, so the
  first-screen card «Этот месяц: X, обычно Y, прогноз Z», KPIs, insights,
  monitor, tiers, charts and the operations table never count them. Cash
  withdrawals are still dropped first, even when marked. A card «🔶 Крупные
  траты» under the operations table lists date, Category, amount as entered
  (≈ base below for another Currency) and comment, newest first, within
  `DASHBOARD_FROM_MONTH`; no Large expenses → no card. A sheet without the
  column builds as before. Details: `dashboard/SPEC.md` §3.3, §4.13. Tests:
  `tests/test_dashboard_large.py`.
- Large expense in statistics (issue #87, spec «Крупная трата», stories 13–14):
  every request of «📊 Статистика» (period, month, «📜 Последние 3 записи»)
  leaves Large expenses out. The Category step has a third button
  «🔶 Только крупные»: the same path (records / period / month, «Разбить по
  валютам?», Conversion) over Large expenses only, header «…, только крупные
  траты». None in the period → «🔶 Крупных трат за этот период нет» (last
  records: «📭 Крупных трат нет»). The choice is `StatQuery.only_large`; the
  menu tap and the Category step start a new `StatQuery`, so it is never
  inherited. Code: `analytics.filter_rows` / `aggregate` / `last_n` /
  `format_last_n` take `large=False` (only unmarked) or `True` (only marked).
- Large expense on the Draft card (issue #86, spec stories 2, 3, 7): voice,
  text and photo Drafts mark each item above `LARGE_EXPENSE_THRESHOLD` (same
  rule as manual input: `large_expense.suggest`, no rate → no mark) when the
  card is created. «🔶 Крупная» next to «✏️ Сумма» / «👤 Кто» (one item) or in
  the item edit (several) sets or removes the mark on any item. The card shows
  «• 🔶 Крупная трата» or «🔶» before the item's comment. Save writes «да» to
  the Data column `Large` (adds the column on the first mark). The mark is not
  recomputed after the amount or Currency is edited. Tests:
  `tests/test_draft_large.py`.
- Large expense, part 1 (issue #83, spec
  `docs/specs/2026-11-large-receipt-reminders.md`): manual input asks
  «🔶 Это крупная трата?» («Да» / «Нет», typed «да»/«нет» too) after the
  Currency and before the comment step, only when the amount is above
  `LARGE_EXPENSE_THRESHOLD` — an amount with its own Currency («1000 EUR»,
  «117 000 дин»; a bare number and the default are €, never `BASE_CURRENCY`,
  so `BASE_CURRENCY=RSD` no longer makes it 1000 дин; a bad value → one setup
  line and 1000 €). Another Currency is converted with `fx.rate`;
  no rate → no question. The decision is `large_expense.suggest` (no Telegram).
  ⚡ Presets never mark. The mark is `ExpenseRecord.large` / `Row.large`, stored
  in the optional Data column `Large` (alias «Крупная»): the bot writes «да» or
  empty and adds the column after the last header on the first marked write
  (`SheetsClient.add_data_column`); `scripts/bootstrap_sheet.py` adds it to a
  new or existing Data sheet. On read any non-empty cell except «нет», «no»,
  «0», «false» is a mark, so hand edits count. «📅 Этот месяц» and the summary
  on the 1st leave Large expenses out of every sum and list them in the
  collapsed block under «🔶 Крупные (не в итоге)» (Category when a row has no
  comment; look B since #104). Undo ignores the column. Draft card, statistics
  and dashboard: see the #86, #87 and #88 bullets above. Tests:
  `tests/test_large_expense.py`.
- Preset without an amount (issue #85, spec «Напоминание о платежах», stories 42–43):
  a row in «ежемесячные траты» of Regular_expenses with no number is a Preset with
  `amount=None` (one-off rows without a number are still skipped). Its Currency is
  the Currency column holding a mark like «?», else `BASE_CURRENCY`. Its ⚡ button
  asks the amount (`State.TYPING_PRESET_AMT`, checks of `amount_input`, home button
  works), then saves with the Preset name as comment. A Currency typed with the
  amount («3500 дин») wins over the sheet. Presets with an amount save at once as
  before. Tests:
  `tests/test_preset_no_amount.py`.
- Logging (issue #26, plan 2.4, C7): the bot, the dashboard build, the failure
  alert and `scripts/` call `family_bot.logs.setup()` once at start; modules use
  `logging.getLogger(__name__)`, no `print` diagnostics (ruff `T20`; `scripts/`
  keep `print` for their report). Level from `LOG_LEVEL` (default INFO). Lines
  go to stderr as `time LEVEL module: message`: Render **Logs**, and
  `dashboard/output/build.log` / `launchd.log` through `run.sh`. Every line,
  tracebacks too, passes `errors.mask_secrets`; `httpx` request lines (URL with
  the token) are held at WARNING. Setup problems stay one WARNING line each.
  Tests: `tests/test_logs.py`.
- Conversion in statistics (issue #34, plan 3.6, Q7): after «Нет» to «Разбить
  по валютам?» the target Currency is chosen with the same picker as manual
  input (Recent currencies with flags, «🌍 Другая», typed «юань» / «GEL»), so
  any Currency of the rate service works, not only those in Data. The pick
  goes to the User's Recent currencies (spec 3.3: the picker bullet covers
  both places). `fx.rate` reads one table per target Currency (`fx.RATES`),
  kept 1 h (one lock per target, so a slow read blocks only that target); a
  failed read is not repeated for 60 s. «EUR» and «€» count as one Currency
  (compared by ISO code). A missing rate gives «❌ Нет курса для пересчёта в
  дин: €, GEL.» — no sum. Details show the rate with 6 significant digits.
  `analytics.unique_currencies` and `StatsFlow(fallback_currencies=…)` are gone.
- Currency choice (issue #33, plan 3.6, F6, Q7–Q9, Q12, Q17–Q19): the manual
  Currency step and «💱 Валюта» in the Draft card show the User's Recent
  currencies with flags («🇷🇸 дин», «🇨🇳 CNY»), then «🌍 Другая»: every
  currency of the rate service in code order, 24 per page. Typed «юань»,
  «лари» or «GEL» finds it (Russian names from Babel, new bot dependency;
  several matches come back as buttons). A new Currency is written to Data as
  its ISO code. Recent currencies are per Telegram account in the new `Users`
  sheet (bot creates it; `scripts/bootstrap_sheet.py` too); a new User gets
  the three most frequent in Data, empty Data → `дин`, `€`, `₽`. Manual input updates them on save, the Draft card on pick. Code:
  `currency_picker.py` (Telegram-free; statistics Conversion uses it too, #34).
  Render needs the new `requirements.txt` (Babel) — the usual Build Command
  installs it. If the rate service is down, «🌍 Другая» lists only the seven
  registry Currencies.
- After a restart (issue #27, plan 2.5, R17, Q15): main-menu buttons (old
  English labels too, `navigation.MENU_ACTIONS`) and 🏠 are conversation
  `entry_points`, so they work without `/start`. `allow_reentry=False`:
  inside a flow its states read the text as before. A button on an old Draft
  card gets an alert «Черновик устарел после обновления бота, отправьте ещё
  раз» (`draft_expense.STALE_TEXT`); the card keeps its text and loses its
  buttons. An old «↩️ Отменить» gets the alert «…пока бот не
  перезапускался…». Typed text («кофе 350») still needs `/start` or a menu
  tap after a restart. Tests: `tests/test_restart.py`.
- Monthly summary (issue #36, plan 4.2, F4): on the 1st at
  `MONTHLY_SUMMARY_HOUR` (default 9) in `TZ_NAME` the bot sends last month's
  summary (same text as «📅 Этот месяц») to `DASHBOARD_CHAT_ID`
  (`monthly_summary.py`, PTB `JobQueue.run_monthly`; the `job-queue` extra
  adds APScheduler). Unset `DASHBOARD_CHAT_ID` → no job and one setup line at
  start (the line also shows in the «not connected to Google Sheets» reply); no
  Sheets → no job. A wrong `TZ_NAME` → the job uses `Europe/Belgrade`. The two
  settings are parsed in `config.py`. A failure is logged, the bot keeps running. The job lives in memory:
  if the bot is down at that hour (deploy, restart, sleeping instance), that
  month is skipped, not sent late.
- Household data in settings, not code (issue #41, plan 5.6, I8):
  `BASE_CURRENCY` (ISO; bot `Config.default_currency` and dashboard `--base`,
  unset → EUR, unknown → setup problem line), `DASHBOARD_FROM_MONTH` (unset →
  all history), `DASHBOARD_SPENDER_ALIASES`, own shops/places for themes in
  git-ignored `dashboard/theme_keywords.json`, launchd label `LAUNCHD_LABEL`
  (env or `.env`, default `local.family-bot.dashboard`). Owner must set
  `BASE_CURRENCY` in `.env` and on Render, and keep the old label in `.env`
  or reinstall the agent (see PR). Open: a few
  test files still use real first names (edited in parallel by #32/#35);
  `clock.DEFAULT_TZ` stays `Europe/Belgrade`.
- Undo and Draft edits (issue #31, plan 3.3, F2): «✅ Сохранено» (manual and
  Draft) has «↩️ Отменить». It deletes the rows of that save, found by their
  values (Data has no row id); a row changed in the sheet is not deleted. Only
  the User's own latest save, within 30 minutes (`undo.UNDO_WINDOW`), and not
  after a restart. The Draft card has «✏️ Сумма» (typed, same checks as the
  manual flow) and «👤 Кто» (Allowlist Spender names). Handler groups are now
  -3 edits, -2 Allowlist, -1 typed Draft amount.
- Russian UI (issue #29, plan 3.1 and 3.5): messages, buttons, errors, hints
  and setup problem lines are Russian; Category names stay as in Config.
  Amounts everywhere (bot replies, Draft card, stats, presets) go through
  `currency.format_amount`: «1 234 дин», decimals only when not zero, comma as
  the decimal mark. Main-menu and home buttons still accept the old English
  labels, because reply keyboards sent before the deploy stay on phones until
  the next menu. `post_init` fills the Telegram command menu (`bot.COMMANDS`).
  The fallback categories used when the Config sheet is empty or unreadable
  (`config.FALLBACK_CATEGORIES`) are Russian (owner's decision): Еда,
  Транспорт, Развлечения, Другое. The bootstrap list for a new sheet
  (`sheets.DEFAULT_CATEGORIES`) stays English: dashboard tiers map those names.
  `tests/test_russian_ui.py` walks the flows and fails on an English word.
- Per-household isolation: `BOT_TOKEN` + sheet + `ALLOWED_USERS`
- Manual expense ConversationHandler (category → amount → currency → comment → date),
  in `flows/manual.py` since issue #24. The home button is handled once, in the
  `ConversationHandler` fallbacks (`navigation.home_fallbacks()`); states are
  the `IntEnum` `conversation_states.State`, and a test checks each has a handler.
  Issue #32 (plan 3.4): the comment step has «✅ Сегодня, без комментария» —
  one tap saves with today's date (`clock.now()`) and an empty comment.
  «кофе 350 дин сегодня» by hand is now 3 taps (menu, Category, this button);
  before it was 4, and 5 when U2 was written (Currency tap, removed in #15).
- Preset (⚡) one-tap save from `Regular_expenses`
- In-bot statistics with FX conversion (`analytics.py`)
- «📅 Этот месяц» (issue #35, plan 4.1): one main-menu tap shows the current
  month (`clock.now()`): total, Categories and the sum per Spender (look B
  since #104, see the top bullet). Each Currency gets its own block;
  Currencies are never added together.
  `analytics.month_summary(rows, "YYYY-MM")` computes it for any month and
  `analytics.format_month_summary` writes the text, so the monthly summary on
  the 1st (issue #36) can call them for the previous month. Phones get the
  new button with the next menu (`/start` or 🏠 after the deploy).
- Voice: Vosk STT + Claude → Draft confirmation card. Audio decode reads PCM
  bytes from the PyAV frame directly: `to_ndarray()` needs numpy, which is not a
  bot dependency, so every voice note failed with «Не разобрал» after the
  move to `uv export` requirements (`tests/test_voice_decode.py`)
- Receipt photo: Claude Vision → same Draft card
- Typed text (issue #30): «кофе 350» in the main menu → same Draft card,
  parsed by `voice_input.extract_expenses` (`text_input.py`). Text without a
  digit or with no expense found gets a hint; a Claude error gets
  «❌ Не разобрал, введите вручную».
- `/whoami`, `/register` hint, `/dashboard` (resends stored Telegram `file_id`)
- Sheet bootstrap: `scripts/bootstrap_sheet.py`
- Webhook (issue #28): Telegram posts to `<RENDER_EXTERNAL_URL>/telegram`, the
  bot token is not in the URL. `run_webhook` gets `secret_token` from
  `WEBHOOK_SECRET`; requests without the header get 403. Unset → random secret
  per start (still works); a value with characters Telegram rejects → one-line
  exit. Render needs `WEBHOOK_SECRET` set by the owner (recommended, not blocking).
- Package layout (issue #23): all Python code is the `family_bot/` package.
  `uv sync` installs it; `family-bot` (`[project.scripts]` →
  `family_bot.bot:main`) starts the bot, `python -m family_bot.dashboard`
  builds the dashboard. `.env`, a relative `GOOGLE_CREDS_PATH`, `models/`
  (Vosk) and `dashboard/` (template, output) still resolve from the repo
  root. `dashboard/run.sh` puts the repo root on `PYTHONPATH`; the launchd
  agent still calls `run.sh`, so an installed agent needs no reinstall.
- Python 3.12 (`.python-version`); deps in `pyproject.toml` groups `bot` /
  `dashboard` / `dev`, locked in `uv.lock`, install with `uv sync`.
  `requirements.txt` and `dashboard/requirements.txt` are exports of the lock
  for pip-format installs (Render Build Command; `dashboard/run.sh` installs it with `uv pip`).
- Currency registry `currency.py` (issue #14): `$` is back on the buttons and
  in voice/receipt snapping, `¥` is yen (JPY), yuan is stored as `CNY`. Lookup
  accepts symbol, ISO code or Russian word. Adding a Currency is one line,
  except the `sheets.REGULAR_HEADERS` bootstrap list (still open).
- Manual input checks (issue #15): the amount must be a finite number above
  zero with at most two decimals (`amount_input.py`, built on `parse_amount`);
  «1 200» and «350 дин» work,
  a Currency typed with the amount skips the Currency step. A Category or
  Currency not on the list re-asks with the buttons. Voice/receipt amounts
  ≤ 0 are dropped. Edited Telegram messages are ignored (handler group -2).
- Drafts have ids (issue #13): each voice/receipt card saves its own Draft,
  all its items go in one `append_rows` call (a failed save can be retried
  without duplicates), a double tap keeps «✅ Сохранено», the category button
  carries the name, and `go_home` keeps open Drafts. Details:
  `docs/ARCHITECTURE.md` → Input pipelines.
- "Today" follows the family time zone (issue #19): `clock.now()` reads
  `TZ_NAME` (default `Europe/Belgrade`). The date buttons, presets, the Draft
  card, voice/receipt prompts, the month list in stats and the Meta
  `updated_at` stamp all use it. The dashboard `generated_at` stays on the
  build Mac's local clock.
- Data writes follow the sheet's header names (column order is free) and go
  `RAW`, so a comment `=…` stays text. `parse_amount` reads `1,234` as 1234
  and `12,5` as 12.5 (rule in `dashboard/SPEC.md` §3.3). Issue #17.
- `dashboard/run.sh` builds in `dashboard/venv/` on Python 3.12 via `uv`; the
  venv is recreated when `dashboard/requirements.txt` changes (stamp file
  `venv/.deps-stamp`). `./run.sh --trial --csv <file>` builds without Claude
  and Telegram.
- Settings load at startup (issue #22): importing `bot` reads no `.env` and
  opens no spreadsheet. `bot.post_init` calls `config.load(os.environ)` and
  keeps the `Config` (Allowlist, `SheetsClient`, categories, Presets, Currency
  buttons) in `bot_data["config"]`; handlers read it via `bot.cfg(context)`.
  Each setup mistake (Google key, `SHEET_NAME`, `ALLOWED_USERS`, `TZ_NAME`) prints as one line «what — what to do», no traceback; `/reloadcats` shows its problems in the reply.
  Without Google Sheets the bot still starts: `/whoami` answers, any other
  message from an allowed User lists the problems. Missing `BOT_TOKEN` stops
  `main()` with one line. `ALLOWED_USERS` accepts a comma inside a name;
  a broken value is a problem line, not a crash. `scripts/test_google_sheets.py`
  reads `.env`. Preset code names follow `CONTEXT.md` (`Config.presets`,
  `regular.load_presets`).
- Unit tests under `tests/` (auth, expense, draft, sheets seam, analytics, …);
  `FakeSheetsClient` lives in `tests/conftest.py`.
- CI: GitHub Actions (`.github/workflows/ci.yml`) runs `pytest`, `ruff check`,
  `mypy` (library modules) and `pip-audit` on every pull request and push to
  `main`, installing with `uv sync --locked` on Python 3.12.
- Statistics flow lives in `flows/stats.py` (issue #16): request state is one
  `StatQuery`, a new request never reuses the previous month or currency,
  `/start` drops it, and every exit returns to the main menu (R7, R8 fixed).
- Error handler (issue #12): `errors.on_error` is registered in
  `bot.build_application()`. A failing handler logs a masked traceback and the
  user gets «❌ Не сохранилось. Попробуйте ещё раз»; a Vosk/Claude failure on
  voice or photo gets «❌ Не разобрал, введите вручную». No raw exception text
  reaches the user (R3, U5 fixed).
- Slow calls do not freeze the bot (issue #18, R11): handlers run Sheets reads
  and writes, FX requests (stats) and `config.load` / `reload` through
  `asyncio.to_thread`. `per_user.PerUserUpdateProcessor` runs updates of
  different people at the same time and one person's updates in order (PTB
  advises `ConversationHandler` without concurrent updates; the per-user lock
  keeps each conversation in order). A Draft is marked closed before its save
  starts and reopened if the save fails, so a double tap still sees
  «✅ Сохранено». Claude clients for voice, text
  and receipt photo use a 30 s timeout and 1 retry
  (`llm.LLM_TIMEOUT_SEC` / `LLM_MAX_RETRIES`); the dashboard's Claude
  client keeps the SDK defaults.
- Dashboard build pipeline under `dashboard/` (see `dashboard/SPEC.md`)
- Shared code for bot and dashboard (issue #25): a Data row is the typed
  `sheet_data.Row` (`date` as `datetime.date`, `amount` as `float`), read by
  `sheet_data.parse_rows` in bot stats and in the dashboard build. Rates come
  from `fx.py` (`fetch_rates` for the dashboard, `rate` for stats; `analytics`
  has no I/O). JSON from Claude replies is parsed by `llm.extract_json_object`
  (voice, photo, typed text, dashboard grouping). In the dashboard JSON a date
  is always `ДД.ММ.ГГГГ` (an ISO-format cell is converted, an unknown format
  becomes empty) and a Currency word such as «евро» maps to its ISO code.
- Dashboard build safety (issue #20): sheet text is escaped (`esc()` in
  `template.html`) and data JSON is embedded with `json_for_script()`, so a
  comment with HTML or `</script>` stays text. A Telegram publish error masks
  `BOT_TOKEN`. A Currency with no FX rate stops the build and lists the rows.
  A failed Claude batch is not cached, the next run retries it.
- Failed dashboard build alert (issue #21): a non-trial `run.sh` build that
  fails sends one Telegram message to `DASHBOARD_CHAT_ID` with the stage and
  exception type (`family_bot/dashboard/failure_alert.py`, stdlib only). Details:
  `dashboard/SPEC.md` §5.1.
- Dashboard on a phone (issue #37): the page fits 375 px, wide tables scroll
  inside their card. «По каждой» with no Currency chosen shows sums per
  Currency (`sumText()` in `template.html`) and never adds дин + € + ₽;
  charts then use the base currency.
- Dashboard first screen (issue #38): above the filters, «Этот месяц: X,
  обычно Y, прогноз Z» and the top Categories against their 3-month average
  for the same days. The build stamps `as_of` (build date). An unfinished
  month is compared with the same days of the previous month in the card,
  insights, monitor trend and optimization takeaway. Formulas:
  `dashboard/SPEC.md` §4.0. Plan 4.3 done.
- Dashboard look (issue #39): light or dark theme follows the system; each
  Category has its own colour (`categoryColor()`, Config sheet order, never
  repeats past 12); monitor brightness is scaled per row; «Динамика», «Состав
  по месяцам», «Средний чек», «Дни недели» and the comment grouping are folded
  in `<details>` at the bottom; hints open and close on tap; hint texts name
  the `--base` currency. The build keeps Category names as in the sheet:
  rent / bills repairs pick an existing name (`repair_category()`), no forced
  `Rent` / `Bills`. Details: `dashboard/SPEC.md` §3.6, §4. Plan 4.4 done.

## Settled (do not re-open as unknowns)

- **No `.env` / `secrets/` in git.** Public repo. A clean checkout cannot call
  Telegram or Sheets. Secrets stay on the machine that runs the bot. Do not
  search the tree for them and do not treat the absence as a defect.
- **No hosting manifest.** No `Procfile`, `render.yaml`, or `Dockerfile`.
  README's Render webhook is optional and unconfirmed. Do not infer a live host
  from the repo.
- **Dashboard build host (documented):** `dashboard/README.md` says the HTML is
  built on this Mac (`run.sh` / `launchd`), then the bot only re-sends the
  stored Telegram `file_id`. The bot process itself is not that Mac job.
- **Claude model id `claude-sonnet-5` is real.** Checked 2026-11-18 against
  [Claude Sonnet 5](https://platform.claude.com/docs/en/models/sonnet-5/overview):
  Claude API id, legacy, available until no earlier than 2027-06-30.
  Anthropic's current recommendation is `claude-sonnet-5-5`. Code default stays
  `claude-sonnet-5` until someone asks to switch. Overrides: `VX_LLM_MODEL`
  (voice), `PX_LLM_MODEL` (receipt), dashboard `--model`.
- **Dashboard spec is complete in-repo.** `dashboard/SPEC.md` covers the
  pipeline (§3), `template.html` (§4), decisions (§6), and a change map (§7).
  Unread sections are unread source, not missing knowledge.

## Open

- New bot look, stage 1 (#100 built: HTML everywhere, `look.py`, button
  styles; #104 built: month summary; #106 built: Payment reminder; the rest is spec only): HTML in every message,
  labelled Draft card with the voice transcript in a quote, one live card for
  manual input and Draft edits (the bot deletes the person's replies and voice
  messages, keeps receipt photos), saved/undone line, coloured buttons
  (green confirm, red cancel), two-row menu, text bars in the month summary,
  «ты» everywhere. Spec: [`docs/specs/2026-11-bot-look.md`](specs/2026-11-bot-look.md);
  research: [`docs/research/2026-11-telegram-formatting.md`](research/2026-11-telegram-formatting.md).
  Mini App is stage 2, no spec yet. Issues #100–#108 (`ready-for-agent`);
  #100 (HTML + button styles) blocks the rest. Rule for every later ticket:
  any new text with human input goes through `look` helpers, or Telegram
  rejects the message («can't parse entities»).

- New features planned (partly built): Large expense (#83 done: manual input
  and «📅 Этот месяц»; #86 Draft card, #87 stats, #88 dashboard done), receipt
  items from the Serbian fiscal QR (#84, #90, #91 open); Payment reminder (#89) built. Spec:
  [`docs/specs/2026-11-large-receipt-reminders.md`](specs/2026-11-large-receipt-reminders.md);
  decision on the QR: [`docs/adr/0001-receipt-items-from-fiscal-qr.md`](adr/0001-receipt-items-from-fiscal-qr.md).
  GitHub issues with `ready-for-agent` carry the work; the first receipt issue
  needs a real receipt from the owner.

- Currency picker (issue #33): Recent currencies are read from `Users` once
  per User per bot process (cache in `user_data`), so a hand edit of `Users`
  shows after a restart. Buttons of an old picker message (manual input or
  statistics Conversion, after the User moved on) get no answer. The Telegram
  side of the picker (`_inline`, typed search, page/pick buttons) is copied in
  `flows/manual.py` and `flows/stats.py`; a shared adapter is a follow-up. Voice/receipt/typed Drafts still snap to
  the seven registry Currencies; only «💱 Валюта» on the card reaches the rest.

- Monthly summary (issue #36): `DASHBOARD_CHAT_ID` is set on Render (owner,
  2026-11-19). The service is on the paid Starter plan, which does not sleep,
  so the job fires if no deploy runs at that hour. First run: 1 December 09:00
  `TZ_NAME`; a real send to Telegram is not verified yet.
- README «Install from scratch» (issue #40): a clean-clone walk with fake keys
  covered `uv sync`, the pip route, `family-bot`, the setup lines and the Vosk
  download. Not walked: the Google Cloud console steps, a real spreadsheet, and
  Render (need real accounts). Two rough edges stay in code: a wrong `BOT_TOKEN` ends in a
  `telegram.error.InvalidToken` traceback, and the setup lines print only after
  Telegram accepts the token (`post_init`); `scripts/bootstrap_sheet.py` prints a
  traceback instead of one line when the key or sheet is wrong.
- Render runs the package build (owner changed the commands 2026-11-19):
  Build Command
  `pip install -r requirements.txt && pip install --no-deps -e . && python -c "from family_bot.voice_input import ensure_model; ensure_model()"`,
  Start Command `family-bot`, env `PYTHON_VERSION=3.12.12` (Render ignored
  `.python-version` and built with 3.11.5 without it), `BASE_CURRENCY`,
  `DASHBOARD_CHAT_ID`. Open check (Q16): the runtime log has no «Downloading
  Vosk model» line on the first voice message (the build log should have it).
- Typed text (issue #30) reaches `bot.text_expense` only inside the
  conversation: after a bot restart the user needs `/start` or a menu button
  first (menu buttons are `entry_points` since issue #27). Text without a digit
  («кофе триста») gets the hint without a Claude call, to avoid paid calls on
  chat text; voice still parses number words. Without `ANTHROPIC_API_KEY`,
  text with a digit gets «❌ Не разобрал, введите вручную».
- Lint and type checks are relaxed so CI passed without product-code edits
  (issue #10). `ruff` uses only its default rules (`E4`, `E7`, `E9`, `F`) with
  per-file ignores; the wider set (`I`, `UP`, `B`) is not on. `mypy` skips
  `bot.py`, does not use `--strict`, and disables named error codes in
  `flow_preset`, `flows.manual`, `navigation`, `photo_input`, `voice_input`. Both
  lists are in `pyproject.toml`; shrink them as modules are cleaned up.
- `tests/test_sheets.py` re-exports `FakeSheetsClient` so branches started
  before the move still import it. Drop the re-export once no branch imports
  the fake from `tests.test_sheets`.
- Doc/code mismatches from spec section 2.8 are fixed in the docs; the last
  one (the dashboard's own row parsing, C3) closed with issue #25.
- Since issue #18 two people's Sheets calls can run in two threads on the
  one shared gspread client (`Config.sheets`). gspread does not document
  thread safety; no problem has been seen or tested.
- `go_home` still clears every other `user_data` key, not only its own flow's
  keys (spec §3.2 «Кнопка «домой»», plan item 2.2).
- Full project review with findings, design, and a staged plan:
  [`docs/specs/2026-11-revision.md`](specs/2026-11-revision.md). Known bugs live
  there (section 2) until fixed; tick plan items as they land.
- Known-bug reproductions live in `tests/known_bugs/` as
  `xfail(strict=True)`, tagged with their finding id. A fix removes the marker;
  `pytest -rx` lists what is still open. R11 (issue #18) was the last marked
  reproduction; no `known_bug` marker is left in the suite.
- The revision plan is done: all 34 GitHub issues (#9–#42) are closed and
  every plan item in the spec is ticked (2026-11-19). The Open items above are
  what is left.

## Only a person knows

- Whether a bot process is up right now, and on which machine.
- Where this household's real `.env` and `secrets/creds.json` live.

## Recent doc work (2026-11-18, later)

- Closed three false unknowns: missing secrets, missing deploy manifest, and
  `claude-sonnet-5` (verified real). SPEC gained a section index.
- Two facts stay outside git: whether a bot process is up, and where the real
  `.env` lives.

## Recent doc work (2026-11-18)

- Added `AGENTS.md`, `CLAUDE.md`, `docs/ARCHITECTURE.md`, this STATUS file
- Cursor rule `.cursor/rules/keep-docs-fresh.mdc` (always apply)
- Pointed `README.md` / `CONTEXT.md` at the agent entry docs

## How to update this file

When finishing work, rewrite facts that changed:

1. Bump **Last updated** / **Updated by**. Set **Product code tip** to the last commit that changed behaviour (a docs-only commit does not move it).
2. Move finished items out of **Open** into **What works** (or delete).
3. Add new risks, deploy notes, or follow-ups under **Open**.
4. One short bullet under a dated subsection if the change needs a breadcrumb.

Do not leave STATUS describing a world the code no longer matches.
