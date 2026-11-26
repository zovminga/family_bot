"""Dashboard build inside the bot: on the 1st of each month and on /build_dashboard.

The job runs the same build as ``dashboard/run.sh`` (``build_dashboard.build_from_env``)
in a worker thread, so the bot keeps answering while it builds. The build sends
the file to ``DASHBOARD_CHAT_ID`` and stores its file_id in the Meta sheet; a
failure sends the short alert there. One build at a time: a lock in ``bot_data``.

The monthly job lives in the ``JobQueue``, in process memory: if the bot is not
running at that hour (deploy, restart), that month's build is skipped, as with
the monthly summary. /build_dashboard builds at any time.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import time
from typing import Any, MutableMapping

from telegram import Update
from telegram.ext import CallbackContext, ContextTypes, JobQueue

from family_bot import config
from family_bot.dashboard import build_dashboard
from family_bot.monthly_summary import DAY_OF_MONTH, job_tz

logger = logging.getLogger(__name__)

JOB_NAME = "dashboard_build"
LOCK_KEY = "dashboard_build_lock"

STARTED = "⏳ Собираю дашборд. Пришлю его в чат дашборда, когда соберётся."
BUSY = "⏳ Дашборд уже собирается. Пришлю, когда будет готов."
NOT_SET = ("❌ Некуда отправить дашборд: нет Google Sheets или <code>DASHBOARD_CHAT_ID</code>. "
           "Что исправить — в логе бота при запуске.")


def build_lock(bot_data: MutableMapping[str, Any]) -> asyncio.Lock:
    """The one lock for dashboard builds of this Application."""
    return bot_data.setdefault(LOCK_KEY, asyncio.Lock())


async def run_build(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Job callback: build and publish the dashboard in a thread.

    A build already running makes this call a no-op. The build logs and
    alerts its own failures and never raises.

    Parameters
    ----------
    context : ContextTypes.DEFAULT_TYPE
        Job context; only ``bot_data`` is used.
    """
    lock = build_lock(context.bot_data)
    if lock.locked():
        logger.info("Dashboard build already running, this one is skipped.")
        return
    async with lock:
        await asyncio.to_thread(build_dashboard.build_from_env)


def schedule(job_queue: JobQueue[CallbackContext], hour: int) -> None:
    """
    Run ``run_build`` on the 1st of each month at ``hour`` family time.

    Parameters
    ----------
    job_queue : JobQueue
        The Application's job queue.
    hour : int
        Hour 0-23 in the family time zone (``TZ_NAME``).
    """
    job_queue.run_monthly(run_build, when=time(hour, tzinfo=job_tz()),
                          day=DAY_OF_MONTH, name=JOB_NAME)


async def build_now(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /build_dashboard — start a build now; the file goes to DASHBOARD_CHAT_ID.

    Parameters
    ----------
    update : Update
        The command message.
    context : ContextTypes.DEFAULT_TYPE
        Handler context with the Config and the job queue.
    """
    msg = update.effective_message
    assert msg is not None and context.job_queue is not None  # command updates have both
    settings: config.Config = context.bot_data[config.KEY]
    if settings.summary_chat_id is None or settings.sheets is None:
        await msg.reply_text(NOT_SET)
        return
    if build_lock(context.bot_data).locked():
        await msg.reply_text(BUSY)
        return
    context.job_queue.run_once(run_build, when=0, name=JOB_NAME)
    await msg.reply_text(STARTED)
