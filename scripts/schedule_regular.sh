#!/usr/bin/env bash
# Manage the monthly launchd schedule for recurring expenses.
#
# launchd is the native macOS scheduler. Unlike cron, if the Mac is asleep or
# off at the scheduled moment, launchd runs the missed job at the next wake — and
# regular.py is idempotent, so a double run in the same month is a no-op.
#
# Usage:
#   ./schedule_regular.sh install     # install & start the monthly agent (1st, 09:05)
#   ./schedule_regular.sh uninstall   # stop & remove the agent
#   ./schedule_regular.sh status      # show whether the agent is loaded
#   ./schedule_regular.sh run         # post recurring expenses now via launchd
#
# The generated plist lives in ~/Library/LaunchAgents and is NOT committed
# (it contains machine-specific absolute paths).
set -euo pipefail

SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FAMILY_DIR="$(cd "$SCRIPTS_DIR/.." && pwd)"
RUN_SH="$SCRIPTS_DIR/run_regular.sh"
LABEL="com.zovminga.familybot.regular"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_OUT="$FAMILY_DIR/regular.log"

# Day-of-month and time the monthly post fires (1st of the month, 09:05).
RUN_DAY="${RUN_DAY:-1}"
RUN_HOUR="${RUN_HOUR:-9}"
RUN_MINUTE="${RUN_MINUTE:-5}"

install_agent() {
  chmod +x "$RUN_SH"
  mkdir -p "$HOME/Library/LaunchAgents"
  cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>$RUN_SH</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Day</key>
        <integer>$RUN_DAY</integer>
        <key>Hour</key>
        <integer>$RUN_HOUR</integer>
        <key>Minute</key>
        <integer>$RUN_MINUTE</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>$LOG_OUT</string>
    <key>StandardErrorPath</key>
    <string>$LOG_OUT</string>
    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
PLIST_EOF

  launchctl unload "$PLIST" 2>/dev/null || true
  launchctl load "$PLIST"
  echo "✅ Installed monthly recurring post: day $RUN_DAY at ${RUN_HOUR}:$(printf '%02d' "$RUN_MINUTE")"
  echo "   Plist: $PLIST"
  echo "   Log:   $LOG_OUT"
}

uninstall_agent() {
  launchctl unload "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  echo "✅ Removed monthly recurring agent."
}

case "${1:-}" in
  install)   install_agent ;;
  uninstall) uninstall_agent ;;
  status)    launchctl list | grep -F "$LABEL" && echo "→ agent is loaded" || echo "→ agent is NOT loaded" ;;
  run)       launchctl start "$LABEL" && echo "▶️  Triggered a recurring post via launchd (see $LOG_OUT)" ;;
  *)         echo "Usage: $0 {install|uninstall|status|run}" ; exit 1 ;;
esac
