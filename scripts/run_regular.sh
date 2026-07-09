#!/usr/bin/env bash
# Post this month's recurring expenses to the Data sheet (idempotent).
#
# Reuses the dashboard virtualenv (it already has gspread/oauth2client/dotenv).
# Safe to run by hand or from launchd; regular.py only posts a given month once
# unless called with --force.
#
# Usage:
#   ./run_regular.sh            # post once for the current month
#   ./run_regular.sh --force    # post again even if this month was done
set -euo pipefail

FAMILY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$FAMILY_DIR/dashboard/venv/bin/python"
[ -x "$PY" ] || PY="python3"

cd "$FAMILY_DIR"
exec "$PY" "$FAMILY_DIR/regular.py" "$@"
