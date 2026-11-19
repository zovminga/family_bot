# Project status

> Living handoff. Agents update this at the end of every meaningful task
> (see `AGENTS.md` → Keep docs fresh). Newest facts on top inside each section.

**Last updated:** 2026-11-19  
**Updated by:** agent session (issue #36 monthly summary on the 1st)  
**Product code tip:** `570b520` (2026-11-19) — household data (base currency, first month, Spender aliases, shops, launchd label) moved to settings (issue #41)  
**Repo:** https://github.com/zovminga/family_bot.git (public)

## Snapshot

Stable Telegram expense bot with Google Sheets backend and a separate offline
HTML dashboard. Core flows work: manual add, presets, stats, voice, receipt
photo, allowlist. Last product/code commit: **2026-08-12**. Local workspace
has a clean tree matching GitHub; no local `.env` in this checkout.

## What works

- Monthly summary (issue #36, plan 4.2, F4): on the 1st at
  `MONTHLY_SUMMARY_HOUR` (default 9) in `TZ_NAME` the bot sends last month's
  summary (same text as «📅 Этот месяц») to `DASHBOARD_CHAT_ID`
  (`monthly_summary.py`, PTB `JobQueue.run_monthly`; the `job-queue` extra
  adds APScheduler). Unset `DASHBOARD_CHAT_ID` → no job and one setup line at
  start. A failure is logged, the bot keeps running. The job lives in memory:
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
  month (`clock.now()`): total, top 3 Categories and the sum per Spender.
  Each Currency gets its own block; Currencies are never added together.
  `analytics.month_summary(rows, "YYYY-MM")` computes it for any month and
  `analytics.format_month_summary` writes the text, so the monthly summary on
  the 1st (issue #36) can call them for the previous month. Phones get the
  new button with the next menu (`/start` or 🏠 after the deploy). Like every
  menu button, it gets no answer after a bot restart until `/start` (see Open).
- Voice: Vosk STT + Claude → Draft confirmation card
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

- Monthly summary (issue #36) needs `DASHBOARD_CHAT_ID` on Render; today it is
  only in the owner's Mac `.env` (dashboard publish). If the Render instance
  sleeps when idle (free plan), the job does not fire on the 1st unless the
  bot is awake at that hour; nobody has checked which plan the service uses.
- README «Install from scratch» (issue #40): a clean-clone walk with fake keys
  covered `uv sync`, the pip route, `family-bot`, the setup lines and the Vosk
  download. Not walked: the Google Cloud console steps, a real spreadsheet, and
  Render (need real accounts). Two rough edges stay in code: a wrong `BOT_TOKEN` ends in a
  `telegram.error.InvalidToken` traceback, and the setup lines print only after
  Telegram accepts the token (`post_init`); `scripts/bootstrap_sheet.py` prints a
  traceback instead of one line when the key or sheet is wrong.
- Render still has the old commands until the owner changes them (issue #23
  PR, README «Deploy»): Build Command
  `pip install -r requirements.txt && pip install --no-deps -e . && python -c "from family_bot.voice_input import ensure_model; ensure_model()"`,
  Start Command `family-bot`. Render ignored `.python-version` on the owner's
  service (built with 3.11.5); env var `PYTHON_VERSION=3.12.12` fixed the build.
  Open check (Q16): after the first deploy, the
  runtime log has no «Downloading Vosk model» line on the first voice message
  (the build log should have it).
- Typed text (issue #30) reaches `bot.text_expense` only inside the
  conversation: after a bot restart the user needs `/start` first (menu texts
  in `entry_points` are plan §3.2 «Состояние»). Text without a digit
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
- Logging is not configured: `errors` logs through the `family_bot.errors`
  logger, which prints to stderr via Python's fallback handler (WARNING and
  above). Other modules still `print` exception text to stdout without
  masking (`bot.py`, `sheets.py`, `fx.py`, `flows/stats.py`); none of
  it reaches the user. Issue #26 switches to `logging`.
- `go_home` still clears every other `user_data` key, not only its own flow's
  keys (spec §3.2 «Кнопка «домой»», plan item 2.2).
- Drafts live only in bot memory: a restart drops them and old cards answer
  «Черновик уже не активен.» (issue #27 changes that text).
- Full project review with findings, design, and a staged plan:
  [`docs/specs/2026-11-revision.md`](specs/2026-11-revision.md). Known bugs live
  there (section 2) until fixed; tick plan items as they land.
- Known-bug reproductions live in `tests/known_bugs/` as
  `xfail(strict=True)`, tagged with their finding id. A fix removes the marker;
  `pytest -rx` lists what is still open. R11 (issue #18) was the last marked
  reproduction; no `known_bug` marker is left in the suite.
- The plan is split into 34 GitHub issues (#9–#42, label `ready-for-agent`)
  with native "blocked by" links. #9 (Python 3.12 + pyproject) is done; issues it
  blocked can start once it merges.

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
