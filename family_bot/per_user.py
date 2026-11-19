"""Update order: different people in parallel, one person's updates in order.

python-telegram-bot handles updates one at a time by default, so a slow Sheets,
FX or Claude call for one person delays everyone. This processor lets updates of
different people run together, while each person's updates still run one after
another: a ConversationHandler sees one user's steps in order, and a double tap
on «Сохранить» waits for the first save to finish.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any, Awaitable, Optional

from telegram.ext import BaseUpdateProcessor

# Upper bound on updates handled at the same time (python-telegram-bot setting).
# A slot is held while an update waits for its user's lock, so one user would
# need this many queued updates to delay others.
MAX_CONCURRENT_UPDATES = 32


def _user_id(update: object) -> Optional[int]:
    """
    Telegram user id of an update.

    Parameters
    ----------
    update : object
        Incoming update.

    Returns
    -------
    Optional[int]
        ``effective_user.id``, or None for updates without a user (they share one lock).
    """
    user = getattr(update, "effective_user", None)
    return getattr(user, "id", None)


class PerUserUpdateProcessor(BaseUpdateProcessor):
    """Serialize updates per Telegram user; run different users concurrently."""

    def __init__(self) -> None:
        super().__init__(MAX_CONCURRENT_UPDATES)
        self._locks: defaultdict[Optional[int], asyncio.Lock] = defaultdict(asyncio.Lock)

    async def do_process_update(self, update: object, coroutine: Awaitable[Any]) -> None:
        """
        Run one update's handlers after this user's earlier updates finish.

        Parameters
        ----------
        update : object
            Incoming update.
        coroutine : Awaitable[Any]
            Handler run for this update, built by python-telegram-bot.
        """
        async with self._locks[_user_id(update)]:
            await coroutine

    # Abstract in BaseUpdateProcessor; this processor holds no resources.
    async def initialize(self) -> None:
        """Nothing to set up."""

    async def shutdown(self) -> None:
        """Nothing to release."""
