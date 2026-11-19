#!/usr/bin/env bash
# On-demand / scheduled build of the family expenses dashboard.
#
# Pulls fresh data straight from Google Sheets and classifies expense comments
# into themes with Claude (Anthropic). Designed to be safe to run both by hand
# and from launchd: it keeps an isolated virtualenv in venv/ on the pinned Python so the
# scheduled monthly build is reproducible and does not touch the system Python.
# The venv is rebuilt from scratch whenever requirements.txt (an export of
# uv.lock) or the version in .python-version changes.
#
# Usage:
#   ./run.sh                 # default monthly build (Sheets + Claude + Telegram)
#   ./run.sh --from ""       # include all history
#   ./run.sh --provider openai
#   ./run.sh --trial --csv sample.csv   # same venv, no Claude, no Telegram
#
# Any extra arguments are forwarded to build_dashboard.py.
#
# A failed build without --trial sends a short alert to the Dashboard chat
# (failure_alert.py): build_dashboard.py alerts for its own errors, the trap
# below covers the steps before it (uv missing, venv install, crash on start).
set -euo pipefail

DASHBOARD_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$DASHBOARD_DIR/venv"
REQUIREMENTS="$DASHBOARD_DIR/requirements.txt"
OUTPUT_DIR="$DASHBOARD_DIR/output"
LOG_FILE="$OUTPUT_DIR/build.log"
# The repo pins one Python version for the bot and the dashboard.
PYTHON_VERSION="$(tr -d '[:space:]' < "$DASHBOARD_DIR/../.python-version")"
# Written into the venv after a successful install; a mismatch means rebuild.
STAMP_FILE="$VENV_DIR/.deps-stamp"

mkdir -p "$OUTPUT_DIR"

log() { printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }

# Must match ALERT_HANDLED_EXIT_CODE in failure_alert.py: build_dashboard.py
# has already tried to send the alert.
ALERT_HANDLED_EXIT_CODE=3
alert_on_failure=1
[ "${1:-}" = "--trial" ] && alert_on_failure=0
stage="запуск run.sh"

on_exit() {
  local code=$?
  if [ "$code" -eq 0 ] || [ "$code" -eq "$ALERT_HANDLED_EXIT_CODE" ] || [ "$alert_on_failure" -eq 0 ]; then
    return
  fi
  # The venv may be the thing that broke; failure_alert.py needs only the stdlib.
  local py="$VENV_DIR/bin/python"
  [ -x "$py" ] || py="$(command -v python3 || true)"
  if [ -z "$py" ]; then
    log "No python3 found — failure alert not sent."
    return
  fi
  "$py" "$DASHBOARD_DIR/failure_alert.py" "$stage (код $code)" 2>&1 | tee -a "$LOG_FILE" || true
}
trap on_exit EXIT

# launchd starts jobs with PATH=/usr/bin:/bin:/usr/sbin:/sbin, where uv's
# default install locations are missing.
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
stage="поиск uv"
if ! command -v uv >/dev/null 2>&1; then
  log "uv not found. Install it: https://docs.astral.sh/uv/getting-started/installation/"
  exit 1
fi

# The stamp ties the venv to the exact dependency set and Python version.
expected_stamp="python=$PYTHON_VERSION requirements=$(shasum -a 256 "$REQUIREMENTS" | cut -d' ' -f1)"
current_stamp="$(cat "$STAMP_FILE" 2>/dev/null || true)"

if [ ! -x "$VENV_DIR/bin/python" ] || [ "$current_stamp" != "$expected_stamp" ]; then
  stage="создание venv"
  log "Recreating virtualenv at $VENV_DIR (Python $PYTHON_VERSION, $REQUIREMENTS)"
  rm -rf "$VENV_DIR"
  # uv downloads a managed Python 3.12 if the machine has none.
  uv venv --quiet --python "$PYTHON_VERSION" "$VENV_DIR"
  uv pip install --quiet --python "$VENV_DIR/bin/python" -r "$REQUIREMENTS"
  printf '%s\n' "$expected_stamp" > "$STAMP_FILE"
fi

if [ "${1:-}" = "--trial" ]; then
  shift
  # Trial build: rule-based themes, no Telegram upload. Pass --csv to skip Sheets.
  build_flags=()
  log "Trial build (no Claude, no Telegram)…"
else
  # --llm --provider anthropic: classify comments into themes with Claude.
  # --publish: after building, upload to Telegram + store file_id so the bot can
  #            re-send the latest dashboard on demand.
  # Data source defaults to Google Sheets (needs family_bot/secrets/creds.json).
  build_flags=(--llm --provider anthropic --publish)
  log "Building dashboard (Google Sheets + Claude classification)…"
fi

stage="запуск build_dashboard.py"
"$VENV_DIR/bin/python" "$DASHBOARD_DIR/build_dashboard.py" \
  ${build_flags[@]+"${build_flags[@]}"} "$@" 2>&1 | tee -a "$LOG_FILE"

log "Done. Output: $OUTPUT_DIR/expenses_dashboard.html"
