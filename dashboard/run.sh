#!/usr/bin/env bash
# On-demand / scheduled build of the family expenses dashboard.
#
# Pulls fresh data straight from Google Sheets and classifies expense comments
# into themes with Claude (Anthropic). Designed to be safe to run both by hand
# and from launchd: it bootstraps an isolated virtualenv on first run so the
# scheduled monthly build is reproducible and does not touch the system Python.
#
# Usage:
#   ./run.sh                 # default monthly build (Sheets + Claude)
#   ./run.sh --from ""       # include all history
#   ./run.sh --provider openai
#
# Any extra arguments are forwarded to build_dashboard.py.
set -euo pipefail

DASHBOARD_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$DASHBOARD_DIR/venv"
OUTPUT_DIR="$DASHBOARD_DIR/output"
LOG_FILE="$OUTPUT_DIR/build.log"

mkdir -p "$OUTPUT_DIR"

# Prefer an explicit interpreter, fall back to python3 on PATH.
PYTHON_BIN="${PYTHON_BIN:-python3}"

log() { printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }

# Bootstrap an isolated venv on first run so scheduled runs are reproducible.
if [ ! -x "$VENV_DIR/bin/python" ]; then
  log "Creating virtualenv at $VENV_DIR"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
  "$VENV_DIR/bin/pip" install --quiet --upgrade pip
  "$VENV_DIR/bin/pip" install --quiet -r "$DASHBOARD_DIR/requirements.txt"
fi

log "Building dashboard (Google Sheets + Claude classification)…"
# --llm --provider anthropic: classify comments into themes with Claude.
# --publish: after building, upload to Telegram + store file_id so the bot can
#            re-send the latest dashboard on demand.
# Data source defaults to Google Sheets (needs family_bot/secrets/creds.json).
"$VENV_DIR/bin/python" "$DASHBOARD_DIR/build_dashboard.py" \
  --llm --provider anthropic --publish "$@" 2>&1 | tee -a "$LOG_FILE"

log "Done. Output: $OUTPUT_DIR/expenses_dashboard.html"
