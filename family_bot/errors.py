"""Bot-wide error reporting: short text for the user, masked details for the log.

``on_error`` is the Application error handler (``add_error_handler``). Handlers
that catch an error themselves (voice, photo, dashboard, stats) call
``log_exception`` and reply with a short text of their own or one of the texts
below, never with the exception text.

Standard library only, imports in Python 3.9 syntax: ``logs`` imports
``mask_secrets`` and ``dashboard/failure_alert.py`` (system python3) imports ``logs``.
"""
from __future__ import annotations

import logging
import os
import re
import traceback
from typing import Any

logger = logging.getLogger("family_bot.errors")

SAVE_FAILED = "❌ Не сохранилось. Попробуй ещё раз"
NOT_PARSED = "❌ Не разобрал. Введи вручную: «💰 Добавить трату»"

MASK = "***"
SECRET_ENV_VARS = ("BOT_TOKEN", "ANTHROPIC_API_KEY")
# Token shapes masked even when the value is not in env (e.g. a stale token).
SECRET_PATTERNS = (
    re.compile(r"\d{5,}:[A-Za-z0-9_-]{30,}"),  # Telegram bot token
    re.compile(r"sk-ant-[A-Za-z0-9_-]+"),  # Anthropic API key
)


def mask_secrets(text: str) -> str:
    """
    Replace bot tokens and API keys in ``text`` with ``***``.

    Parameters
    ----------
    text : str
        Any text bound for the log.

    Returns
    -------
    str
        The text with secret values from env and token-shaped strings masked.
    """
    for var in SECRET_ENV_VARS:
        value = os.getenv(var)
        if value:
            text = text.replace(value, MASK)
    for pattern in SECRET_PATTERNS:
        text = pattern.sub(MASK, text)
    return text


def log_exception(where: str, error: BaseException) -> None:
    """
    Log ``error`` with its traceback, secrets masked.

    Parameters
    ----------
    where : str
        Short name of the failed step, first word of the log line.
    error : BaseException
        The caught exception; chained causes are logged too.
    """
    details = "".join(traceback.format_exception(error))
    logger.error("%s failed:\n%s", where, mask_secrets(details))


async def on_error(update: object, context: Any) -> None:
    """
    Application error handler: log the error, tell the user in short.

    Parameters
    ----------
    update : object
        The update that failed, or None for errors outside an update.
    context : Any
        PTB callback context; ``context.error`` holds the exception.
    """
    log_exception("Update handler", context.error)
    message = getattr(update, "effective_message", None)
    if message is None:
        return
    try:
        await message.reply_text(SAVE_FAILED)
    except Exception as reply_error:  # noqa: BLE001 - nothing left to tell the user
        log_exception("Error reply", reply_error)
