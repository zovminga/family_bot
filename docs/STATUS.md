# Project status

> Living handoff. Agents update this at the end of every meaningful task
> (see `AGENTS.md` → Keep docs fresh). Newest facts on top inside each section.

**Last updated:** 2026-11-19  
**Updated by:** agent session (issue #14 currency registry)  
**Product code tip:** `a4364a0` (2026-11-19) — currency registry `currency.py`, `$` and `CNY` back  
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
- Python 3.12 (`.python-version`); deps in `pyproject.toml` groups `bot` /
  `dashboard` / `dev`, locked in `uv.lock`, install with `uv sync`.
  `requirements.txt` and `dashboard/requirements.txt` are exports of the lock
  for pip-only hosts (Render Build Command, `dashboard/run.sh`).
- Currency registry `currency.py` (issue #14): `$` is back on the buttons and
  in voice/receipt snapping, `¥` is yen (JPY), yuan is stored as `CNY`. Lookup
  accepts symbol, ISO code or Russian word. Adding a Currency is one line,
  except the `sheets.REGULAR_HEADERS` bootstrap list (left to issue #17).
- Unit tests under `tests/` (auth, expense, draft, sheets seam, analytics, …)
- Dashboard build pipeline under `dashboard/` (see `dashboard/SPEC.md`)

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

- Doc/code mismatches from spec section 2.8 are fixed in the docs, except three
  recorded as open: stats not returning home (R7, R8, see
  `docs/ARCHITECTURE.md`), the dashboard's own row parsing (C3, see
  `docs/ARCHITECTURE.md`), and Preset code names (`DEFAULTS`, `load_defaults`,
  see `CONTEXT.md`).
- Full project review with findings, design, and a staged plan:
  [`docs/specs/2026-11-revision.md`](specs/2026-11-revision.md). Known bugs live
  there (section 2) until fixed; tick plan items as they land.
- Known-bug reproductions live in `tests/known_bugs/` as
  `xfail(strict=True)`, tagged with their finding id. A fix removes the marker;
  `pytest -rx` lists what is still open.
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
