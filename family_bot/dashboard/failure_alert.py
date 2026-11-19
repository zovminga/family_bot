#!/usr/bin/env python3
"""Telegram alert for a failed dashboard build (finding D5).

Sends one short message to the Dashboard chat (DASHBOARD_CHAT_ID via BOT_TOKEN,
the same pair `--publish` uses). The reason is a stage name plus an exception
type, never the exception text: build errors can quote sheet rows.

Standard library only, and Python 3.9 syntax: run.sh also calls this file with
the system python3 when the venv could not be built. It imports family_bot.logs,
which imports family_bot.errors; both use only the standard library.

CLI (used by run.sh):  python3 -m family_bot.dashboard.failure_alert "<reason>"
"""
from __future__ import annotations

import http.client
import logging
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from family_bot import logs

logger = logging.getLogger(__name__)

# .env sits in the repo root: family_bot/dashboard/ is two levels below it.
ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
# build_dashboard.py exits with this code after it has tried to send the alert,
# so run.sh does not send a second one.
ALERT_HANDLED_EXIT_CODE = 3
MAX_REASON_LEN = 200
SEND_TIMEOUT_SEC = 30
TOKEN_MASK = "<BOT_TOKEN>"
UNKNOWN_REASON = "причина неизвестна"

Sender = Callable[[str, str, str], None]


def failure_text(reason: str, token: str) -> str:
    """
    Build the alert text.

    Parameters
    ----------
    reason : str
        Stage and exception type; masked and cut to MAX_REASON_LEN.
    token : str
        Bot token to replace with TOKEN_MASK.

    Returns
    -------
    str
        Message text with the reason and where the log is.
    """
    if token:
        reason = reason.replace(token, TOKEN_MASK)
    if len(reason) > MAX_REASON_LEN:
        reason = reason[:MAX_REASON_LEN] + "…"
    return ("⚠️ Сборка дашборда не удалась: " + reason + "\n"
            "Бот отдаёт прошлую версию. Подробности — dashboard/output/launchd.log "
            "(или build.log при ручном запуске).")


def post_message(token: str, chat_id: str, text: str) -> None:
    """
    Send a message with the Bot API sendMessage method.

    Parameters
    ----------
    token : str
        Bot token.
    chat_id : str
        Target chat.
    text : str
        Message text.
    """
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage", data=data)
    with urllib.request.urlopen(request, timeout=SEND_TIMEOUT_SEC):
        pass


def _env_file_values(path: Path) -> dict[str, str]:
    """
    Read KEY=VALUE pairs from a .env file.

    Parameters
    ----------
    path : Path
        The .env file; a missing file gives an empty dict.

    Returns
    -------
    dict[str, str]
        Values without an `export ` prefix, a ` #` comment after an
        unquoted value, or surrounding quotes.
    """
    if not path.is_file():
        return {}
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if value[:1] not in ("'", '"'):
            value = value.split(" #", 1)[0].strip()
        values[key] = value.strip("'\"")
    return values


def send_failure_alert(reason: str, send: Optional[Sender] = None) -> bool:
    """
    Send the failure alert to DASHBOARD_CHAT_ID.

    Never raises: the alert must not hide the build error it reports.

    Parameters
    ----------
    reason : str
        Stage and exception type, never the exception text.
    send : Sender, optional
        Transport; defaults to post_message. Tests pass a fake.

    Returns
    -------
    bool
        True when the message was sent.
    """
    file_values = _env_file_values(ENV_PATH)
    token = os.getenv("BOT_TOKEN") or file_values.get("BOT_TOKEN", "")
    chat_id = os.getenv("DASHBOARD_CHAT_ID") or file_values.get("DASHBOARD_CHAT_ID", "")
    if not token or not chat_id:
        logger.warning("⚠️  BOT_TOKEN/DASHBOARD_CHAT_ID not set — failure alert not sent.")
        return False
    try:
        (send or post_message)(token, chat_id, failure_text(reason, token))
    except (OSError, ValueError, http.client.HTTPException) as exc:
        # urllib errors quote the request URL, which holds the token.
        logger.warning("⚠️  Failure alert not sent: %s", str(exc).replace(token, TOKEN_MASK))
        return False
    logger.info("→ Failure alert sent to Telegram.")
    return True


def main(argv: list[str]) -> int:
    """
    CLI entry point for run.sh.

    Parameters
    ----------
    argv : list[str]
        Reason words.

    Returns
    -------
    int
        Always 0: a failed alert must not change the build's exit code.
    """
    logs.setup()
    reason = " ".join(argv).strip() or UNKNOWN_REASON
    send_failure_alert(reason)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
