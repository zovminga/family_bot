"""One logging setup for every entry point: level from LOG_LEVEL, secrets masked.

Entry points (``family-bot``, ``python -m family_bot.dashboard``, the failure
alert, ``scripts/``) call ``setup()`` once after reading ``.env``; modules only
do ``logger = logging.getLogger(__name__)``.

Standard library only, Python 3.9 syntax: ``dashboard/failure_alert.py`` runs
under the system python3 when the venv is broken.
"""
from __future__ import annotations

import logging
import os
import sys
from typing import IO, Mapping, Optional

from family_bot.errors import mask_secrets

LOG_LEVEL_ENV = "LOG_LEVEL"
DEFAULT_LEVEL = "INFO"
LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
# One record = one line (tracebacks aside). Render adds its own time stamp, but
# build.log / launchd.log on the Mac have only this one.
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
# httpx logs each Telegram request URL, which holds the bot token, at INFO.
QUIET_LOGGERS = ("httpx",)


class MaskingFormatter(logging.Formatter):
    """Formatter that masks bot tokens and API keys in the whole formatted record."""

    def format(self, record: logging.LogRecord) -> str:
        return mask_secrets(super().format(record))


def setup(env: Optional[Mapping[str, str]] = None, stream: Optional[IO[str]] = None) -> None:
    """
    Send log records to ``stream`` at the level named by ``LOG_LEVEL``.

    Calling it again replaces the handler it added before, so a record is
    never written twice.

    Parameters
    ----------
    env : Mapping[str, str], optional
        Settings; defaults to ``os.environ``.
    stream : IO[str], optional
        Where lines go; defaults to stderr.
    """
    env = os.environ if env is None else env
    raw = (env.get(LOG_LEVEL_ENV) or DEFAULT_LEVEL).strip()
    name = raw.upper() if raw.upper() in LEVELS else DEFAULT_LEVEL

    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    handler.setFormatter(MaskingFormatter(LOG_FORMAT, DATE_FORMAT))
    handler.set_name(__name__)
    root = logging.getLogger()
    for old in [h for h in root.handlers if h.get_name() == __name__]:
        root.removeHandler(old)
    root.addHandler(handler)
    root.setLevel(getattr(logging, name))
    for quiet in QUIET_LOGGERS:
        logging.getLogger(quiet).setLevel(logging.WARNING)

    if name != raw.upper():
        logging.getLogger(__name__).warning(
            "%s=%s is not a level (%s), using %s", LOG_LEVEL_ENV, raw, ", ".join(LEVELS), name)
