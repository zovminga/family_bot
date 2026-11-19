"""Bot-wide error reporting: short text for the user, masked details for the log.

``on_error`` is the Application error handler (``add_error_handler``). Handlers
that catch an error themselves (voice, photo, dashboard) call ``log_exception``
and reply with one of the user texts below, never with the exception text.
"""
from __future__ import annotations

import logging
import os
import re
import traceback
from typing import Any

logger = logging.getLogger("family_bot.errors")

SAVE_FAILED = "❌ Не сохранилось. Попробуйте ещё раз"
NOT_PARSED = "❌ Не разобрал, введите вручную"

MASK = "***"
SECRET_ENV_VARS = ("BOT_TOKEN", "ANTHROPIC_API_KEY")
# Token shapes masked even when the value is not in env (e.g. a stale token).
SECRET_PATTERNS = (
    re.compile(r"\d{5,}:[A-Za-z0-9_-]{30,}"),  # Telegram bot token
    re.compile(r"sk-ant-[A-Za-z0-9_-]+"),  # Anthropic API key
)


def mask_secrets(text: str) -> str:
    """Replace bot tokens and API keys in ``text`` with ``***``."""
    for var in SECRET_ENV_VARS:
        value = os.getenv(var)
        if value:
            text = text.replace(value, MASK)
    for pattern in SECRET_PATTERNS:
        text = pattern.sub(MASK, text)
    return text


def log_exception(where: str, error: BaseException) -> None:
    """Log ``error`` with its traceback, secrets masked."""
    details = "".join(traceback.format_exception(error))
    logger.error("%s failed:\n%s", where, mask_secrets(details))


async def on_error(update: object, context: Any) -> None:
    """Application error handler: log the error, tell the user in short."""
    log_exception("Update handler", context.error)
    message = getattr(update, "effective_message", None)
    if message is None:
        return
    try:
        await message.reply_text(SAVE_FAILED)
    except Exception as reply_error:  # noqa: BLE001 - nothing left to tell the user
        log_exception("Error reply", reply_error)
