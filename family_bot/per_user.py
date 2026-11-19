"""Update order: different people in parallel, one person's updates in order.

python-telegram-bot handles updates one at a time by default, so a slow Sheets,
FX or Claude call for one person delays everyone. This processor lets updates of
different people run together, while each person's updates still run one after
another (a double tap on «Сохранить» cannot race itself).
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any, Awaitable, Optional

from telegram.ext import BaseUpdateProcessor

# Upper bound on updates handled at the same time (python-telegram-bot setting).
MAX_CONCURRENT_UPDATES = 32


def _user_id(update: object) -> Optional[int]:
    user = getattr(update, "effective_user", None)
    return getattr(user, "id", None)


class PerUserUpdateProcessor(BaseUpdateProcessor):
    """Serialize updates per Telegram user; run different users concurrently."""

    def __init__(self, max_concurrent_updates: int = MAX_CONCURRENT_UPDATES) -> None:
        super().__init__(max_concurrent_updates)
        self._locks: defaultdict[Optional[int], asyncio.Lock] = defaultdict(asyncio.Lock)

    async def do_process_update(self, update: object, coroutine: Awaitable[Any]) -> None:
        async with self._locks[_user_id(update)]:
            await coroutine

    async def initialize(self) -> None:
        pass

    async def shutdown(self) -> None:
        pass
