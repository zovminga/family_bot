# AGENTS — family_bot

Telegram bot + offline HTML dashboard for one household's expenses in Google Sheets.
Each install is isolated: own `BOT_TOKEN`, own spreadsheet, own `ALLOWED_USERS`.

## Start every session here

1. Read [`docs/STATUS.md`](docs/STATUS.md) — living project state (what is true now).
2. Use [`CONTEXT.md`](CONTEXT.md) terms in speech and code (Expense, Draft, Preset, Spender…).
3. Open deeper docs only when the task needs them (pointers below).

A missing `.env` or `secrets/` is expected (public repo). No `Procfile` /
`render.yaml` / `Dockerfile` means bot liveness is outside git. Do not report
those as project gaps, and do not report a file you did not finish reading as
unknown — jump to the section the task needs (dashboard index is at the top of
`dashboard/SPEC.md`).

## Work tracking

Planned work lives in GitHub Issues with the `ready-for-agent` label. Each issue
has acceptance criteria and native "blocked by" links; start only an issue whose
blockers are closed. One issue = one branch = one PR whose body says
`Closes #<n>`. Source plan: [`docs/specs/2026-11-revision.md`](docs/specs/2026-11-revision.md).

## Doc map

| Reach when… | File |
|-------------|------|
| Need current state, open work, deploy reality | [`docs/STATUS.md`](docs/STATUS.md) |
| Need module seams, flows, sheet schema | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) |
| Need domain words / forbidden synonyms | [`CONTEXT.md`](CONTEXT.md) |
| Installing or running the bot | [`README.md`](README.md) |
| Building or changing the HTML dashboard | [`dashboard/SPEC.md`](dashboard/SPEC.md) (index at top; §3 pipeline, §4 `template.html`, §7 where to edit), [`dashboard/README.md`](dashboard/README.md) |
| Env var names | [`.env.example`](.env.example) |

## Keep docs fresh (mandatory)

Treat docs as part of the deliverable. A coding task is **not done** until docs match the code.

After any change that affects behaviour, structure, deploy, or open work:

1. Update [`docs/STATUS.md`](docs/STATUS.md): date, what changed, open threads, blockers. **Product code tip** moves only when behaviour changes; a docs-only commit leaves it.
2. If modules, flows, sheet schema, or seams moved — update [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
3. If domain language shifted — update [`CONTEXT.md`](CONTEXT.md).
4. If dashboard pipeline / formulas / file tree shifted — update [`dashboard/SPEC.md`](dashboard/SPEC.md).
5. If install/run steps shifted — update [`README.md`](README.md) and/or [`.env.example`](.env.example).

**Done when:** a cold agent reading only `AGENTS.md` → `STATUS.md` could continue without re-discovering what you just changed.

Skip the STATUS bump only for pure typo/format edits that change no facts about the project.

## Hard constraints

- Repo is **public**. Never commit `.env`, `secrets/`, `creds.json`, dashboard `output/`, real CSVs, or tokens.
- Code comments and commit messages: **English**. Dashboard narrative docs may stay Russian.
- Prefer deepening modules over growing `bot.py`. Telegram I/O stays in adapters; domain logic in libraries (`expense`, `draft_expense`, `sheets`, `analytics`, …).
- All Data-sheet writes go through `ExpenseRecord` + `append_expense` (`expense.py`).
- Sheets access goes through `SheetsClient` (`sheets.py`), not raw gspread in callers.
- Allowlist is required for use; `/whoami` stays public so new members can send their id.

## Quick orientation

```
bot.py              Telegram wiring (handlers, ConversationHandler, webhook/polling)
auth.py             ALLOWED_USERS parse + gate
expense.py          ExpenseRecord + append
draft_expense.py    Voice/photo confirmation card
sheets.py           SheetsClient seam
sheet_data.py       Headers, dates, currency maps
analytics.py        Stats over normalized rows (no I/O)
voice_input.py      Vosk STT + Claude extract
photo_input.py      Claude Vision receipt → drafts
regular.py          Regular_expenses → presets
flow_preset.py      ⚡ preset conversation steps
navigation.py       go_home / home button
conversation_states.py  ConversationHandler state ints
dashboard/          Offline HTML build + SPEC
scripts/            bootstrap_sheet.py, connectivity check
tests/              pytest (no live Sheets/Telegram)
```

Default branch: `main` @ `origin` → `https://github.com/zovminga/family_bot.git`.
