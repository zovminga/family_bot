#!/usr/bin/env python3
"""Telegram alert for a failed dashboard build (finding D5).

Sends one short message to the Dashboard chat (DASHBOARD_CHAT_ID via BOT_TOKEN,
the same pair `--publish` uses). The reason is a stage name plus an exception
type, never the exception text: build errors can quote sheet rows.

Standard library only, and Python 3.9 syntax: run.sh also calls this file with
the system python3 when the venv could not be built.

CLI (used by run.sh):  python3 failure_alert.py "<reason>"
"""
from __future__ import annotations

import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Optional

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
# build_dashboard.py exits with this code after it has tried to send the alert,
# so run.sh does not send a second one.
ALERT_HANDLED_EXIT_CODE = 3
MAX_REASON_LEN = 200
SEND_TIMEOUT_SEC = 30
TOKEN_MASK = "<BOT_TOKEN>"

Sender = Callable[[str, str, str], None]


def failure_text(reason: str, token: str) -> str:
    """Alert text: the reason (masked, cut to MAX_REASON_LEN) and where the log is."""
    if token:
        reason = reason.replace(token, TOKEN_MASK)
    if len(reason) > MAX_REASON_LEN:
        reason = reason[:MAX_REASON_LEN] + "…"
    return ("⚠️ Сборка дашборда не удалась: " + reason + "\n"
            "Бот отдаёт прошлую версию. Подробности — dashboard/output/launchd.log "
            "(или build.log при ручном запуске).")


def post_message(token: str, chat_id: str, text: str) -> None:
    """Send `text` to `chat_id` with the Bot API sendMessage method."""
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage", data=data)
    with urllib.request.urlopen(request, timeout=SEND_TIMEOUT_SEC):
        pass


def _env_file_values(path: Path) -> dict[str, str]:
    """KEY=VALUE pairs from a .env file; quotes around a value are dropped."""
    if not path.is_file():
        return {}
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def send_failure_alert(reason: str, send: Optional[Sender] = None) -> bool:
    """Send the alert; return True when Telegram accepted it.

    Never raises: the alert must not hide the build error it reports.
    """
    file_values = _env_file_values(ENV_PATH)
    token = os.getenv("BOT_TOKEN") or file_values.get("BOT_TOKEN", "")
    chat_id = os.getenv("DASHBOARD_CHAT_ID") or file_values.get("DASHBOARD_CHAT_ID", "")
    if not token or not chat_id:
        print("⚠️  BOT_TOKEN/DASHBOARD_CHAT_ID not set — failure alert not sent.",
              file=sys.stderr)
        return False
    try:
        (send or post_message)(token, chat_id, failure_text(reason, token))
    except (OSError, ValueError) as exc:
        # urllib errors quote the request URL, which holds the token.
        print("⚠️  Failure alert not sent: " + str(exc).replace(token, TOKEN_MASK),
              file=sys.stderr)
        return False
    print("→ Failure alert sent to Telegram.", file=sys.stderr)
    return True


def main(argv: list[str]) -> int:
    reason = " ".join(argv).strip() or "причина неизвестна"
    send_failure_alert(reason)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
