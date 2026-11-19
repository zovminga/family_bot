#!/usr/bin/env bash
# On-demand / scheduled build of the family expenses dashboard.
#
# Pulls fresh data straight from Google Sheets and classifies expense comments
# into themes with Claude (Anthropic). Designed to be safe to run both by hand
# and from launchd: it keeps an isolated Python 3.12 virtualenv in venv/ so the
# scheduled monthly build is reproducible and does not touch the system Python.
# The venv is rebuilt from scratch whenever requirements.txt (an export of
# uv.lock) or the Python version below changes.
#
# Usage:
#   ./run.sh                 # default monthly build (Sheets + Claude + Telegram)
#   ./run.sh --from ""       # include all history
#   ./run.sh --provider openai
#   ./run.sh --trial --csv sample.csv   # same venv, no Claude, no Telegram
#
# Any extra arguments are forwarded to build_dashboard.py.
set -euo pipefail

DASHBOARD_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$DASHBOARD_DIR/venv"
REQUIREMENTS="$DASHBOARD_DIR/requirements.txt"
OUTPUT_DIR="$DASHBOARD_DIR/output"
LOG_FILE="$OUTPUT_DIR/build.log"
# Must match .python-version and requires-python in pyproject.toml.
PYTHON_VERSION="3.12"
# Written into the venv after a successful install; a mismatch means rebuild.
STAMP_FILE="$VENV_DIR/.deps-stamp"

mkdir -p "$OUTPUT_DIR"

log() { printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }

# launchd starts jobs with PATH=/usr/bin:/bin:/usr/sbin:/sbin, where uv's
# default install locations are missing.
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
  log "uv not found. Install it: https://docs.astral.sh/uv/getting-started/installation/"
  exit 1
fi

# The stamp ties the venv to the exact dependency set and Python version.
expected_stamp="python=$PYTHON_VERSION requirements=$(shasum -a 256 "$REQUIREMENTS" | cut -d' ' -f1)"
current_stamp="$(cat "$STAMP_FILE" 2>/dev/null || true)"

if [ ! -x "$VENV_DIR/bin/python" ] || [ "$current_stamp" != "$expected_stamp" ]; then
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

"$VENV_DIR/bin/python" "$DASHBOARD_DIR/build_dashboard.py" \
  ${build_flags[@]+"${build_flags[@]}"} "$@" 2>&1 | tee -a "$LOG_FILE"

log "Done. Output: $OUTPUT_DIR/expenses_dashboard.html"
