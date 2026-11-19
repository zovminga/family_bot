"""Last month's summary, sent by the bot on the 1st of each month (plan 4.2, F4).

The job lives in the python-telegram-bot ``JobQueue``, that is, in process
memory. If the bot is not running at the scheduled moment (deploy, restart,
a sleeping host), that month's summary is skipped, not sent late: nothing
records whether it went out, so a catch-up on start could send it twice.
The family can still open the month in «📊 Статистика».
"""
from __future__ import annotations

import asyncio
from datetime import datetime, time, tzinfo
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil.relativedelta import relativedelta
from telegram.ext import CallbackContext, ContextTypes, JobQueue

from family_bot import analytics
from family_bot import clock
from family_bot import errors
from family_bot.sheet_data import MONTH_FMT, Row

# Chat and hour settings are parsed in config.py (DASHBOARD_CHAT_ID, MONTHLY_SUMMARY_HOUR).
DAY_OF_MONTH = 1
JOB_NAME = "monthly_summary"

LoadRows = Callable[[], list[Row]]


def previous_month(today: datetime) -> str:
    """
    The month before ``today``.

    Parameters
    ----------
    today : datetime
        Family wall-clock time (``clock.now()``).

    Returns
    -------
    str
        ``YYYY-MM`` (``MONTH_FMT``).
    """
    return (today.replace(day=1) - relativedelta(months=1)).strftime(MONTH_FMT)


async def send_summary(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Job callback: send last month's summary to the job's chat.

    ``context.job.data`` is the ``LoadRows`` source; it runs in a thread.
    A failure is logged (secrets masked) and the bot keeps running.

    Parameters
    ----------
    context : ContextTypes.DEFAULT_TYPE
        Job context from ``JobQueue``.
    """
    job = context.job
    assert job is not None and job.chat_id is not None  # set by schedule()
    load_rows: LoadRows = job.data  # type: ignore[assignment]
    try:
        rows = await asyncio.to_thread(load_rows)
        month = previous_month(clock.now())
        text = analytics.format_month_summary(analytics.month_summary(rows, month))
        await context.bot.send_message(job.chat_id, text)
    except Exception as e:  # noqa: BLE001 - a failed summary must not stop the bot
        errors.log_exception("Monthly summary", e)


def _job_tz() -> tzinfo:
    """Family zone; ``DEFAULT_TZ`` when ``TZ_NAME`` is wrong (config already printed that line)."""
    try:
        return clock.family_tz()
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(clock.DEFAULT_TZ)


def schedule(job_queue: JobQueue[CallbackContext], chat_id: int, hour: int,
             load_rows: LoadRows) -> None:
    """
    Run ``send_summary`` on the 1st of each month at ``hour`` family time.

    Parameters
    ----------
    job_queue : JobQueue
        The Application's job queue.
    chat_id : int
        Telegram chat for the summary (``DASHBOARD_CHAT_ID``).
    hour : int
        Hour 0-23 in the family time zone (``TZ_NAME``).
    load_rows : LoadRows
        Reads Data rows; blocking, called in a thread.
    """
    job_queue.run_monthly(
        send_summary,
        when=time(hour, tzinfo=_job_tz()),
        day=DAY_OF_MONTH,
        data=load_rows,
        name=JOB_NAME,
        chat_id=chat_id,
    )
