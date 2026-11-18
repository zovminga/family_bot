# Project status

> Living handoff. Agents update this at the end of every meaningful task
> (see `AGENTS.md` → Keep docs fresh). Newest facts on top inside each section.

**Last updated:** 2026-11-18  
**Updated by:** agent session (STATUS tip after docs landed on main)  
**Product code tip:** `311bb67` (2026-08-12) — last behaviour change  
**Docs on main:** yes — PR #3 merged as `8a36741`  
**Repo:** https://github.com/zovminga/family_bot.git (public)

## Snapshot

Stable Telegram expense bot with Google Sheets backend and a separate offline
HTML dashboard. Core flows work: manual add, presets, stats, voice, receipt
photo, allowlist. Last product/code commit: **2026-08-12**. Local workspace
has a clean tree matching GitHub; no local `.env` in this checkout.

## What works

- Per-household isolation: `BOT_TOKEN` + sheet + `ALLOWED_USERS`
- Manual expense ConversationHandler (category → amount → currency → comment → date)
- Preset (⚡) one-tap save from `Regular_expenses`
- In-bot statistics with FX conversion (`analytics.py`)
- Voice: Vosk STT + Claude → Draft confirmation card
- Receipt photo: Claude Vision → same Draft card
- `/whoami`, `/register` hint, `/dashboard` (resends stored Telegram `file_id`)
- Sheet bootstrap: `scripts/bootstrap_sheet.py`
- Unit tests under `tests/` (auth, expense, draft, sheets seam, analytics, …)
- Dashboard build pipeline under `dashboard/` (see `dashboard/SPEC.md`)

## Open / unknown

- **Runtime deploy:** README documents optional Render webhook; whether a live
  Render (or other) instance is running is **not recorded in-repo**. Confirm
  outside git before assuming the bot is online.
- **This checkout** has no `.env` / `secrets/` — cannot run against real Sheets
  until configured locally.
- Dashboard `SPEC.md` narrative is largely still valid; file-tree section was
  refreshed 2026-11-18 to match the deepened bot modules.

## Recent doc work (2026-11-18)

- Added `AGENTS.md`, `CLAUDE.md`, `docs/ARCHITECTURE.md`, this STATUS file
- Cursor rule `.cursor/rules/keep-docs-fresh.mdc` (always apply)
- Pointed `README.md` / `CONTEXT.md` at the agent entry docs

## How to update this file

When finishing work, rewrite facts that changed:

1. Bump **Last updated** / **Updated by**. Set **Product code tip** to the last commit that changed behaviour (a docs-only commit does not move it). Note a docs merge under **Docs on main** when handoff files land.
2. Move finished items out of **Open** into **What works** (or delete).
3. Add new risks, deploy notes, or follow-ups under **Open / unknown**.
4. One short bullet under a dated subsection if the change needs a breadcrumb.

Do not leave STATUS describing a world the code no longer matches.
