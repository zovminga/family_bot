#!/usr/bin/env bash
# Manage the monthly launchd schedule for the expenses dashboard build.
#
# launchd is the native macOS scheduler. Unlike cron, if the Mac is asleep or
# off at the scheduled moment, launchd runs the missed job at the next wake.
#
# Usage:
#   ./schedule.sh install     # install & start the monthly agent (1st, 09:00)
#   ./schedule.sh uninstall   # stop & remove the agent
#   ./schedule.sh status      # show whether the agent is loaded
#   ./schedule.sh run         # trigger a build right now via launchd
#
# The generated plist lives in ~/Library/LaunchAgents and is NOT committed
# (it contains machine-specific absolute paths).
set -euo pipefail

DASHBOARD_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_SH="$DASHBOARD_DIR/run.sh"
LABEL="com.zovminga.familybot.dashboard"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_OUT="$DASHBOARD_DIR/output/launchd.log"

# Day-of-month and time the monthly build fires (1st of the month, 09:00).
RUN_DAY="${RUN_DAY:-1}"
RUN_HOUR="${RUN_HOUR:-9}"
RUN_MINUTE="${RUN_MINUTE:-0}"

install_agent() {
  chmod +x "$RUN_SH"
  mkdir -p "$DASHBOARD_DIR/output" "$HOME/Library/LaunchAgents"
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

  # Reload if already present, then load.
  launchctl unload "$PLIST" 2>/dev/null || true
  launchctl load "$PLIST"
  echo "✅ Installed monthly build: day $RUN_DAY at ${RUN_HOUR}:$(printf '%02d' "$RUN_MINUTE")"
  echo "   Plist: $PLIST"
  echo "   Log:   $LOG_OUT"
}

uninstall_agent() {
  launchctl unload "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  echo "✅ Removed monthly build agent."
}

case "${1:-}" in
  install)   install_agent ;;
  uninstall) uninstall_agent ;;
  status)    launchctl list | grep -F "$LABEL" && echo "→ agent is loaded" || echo "→ agent is NOT loaded" ;;
  run)       launchctl start "$LABEL" && echo "▶️  Triggered a build via launchd (see $LOG_OUT)" ;;
  *)         echo "Usage: $0 {install|uninstall|status|run}" ; exit 1 ;;
esac
