#!/usr/bin/env python3
"""Live card: one bot message that a step-by-step flow keeps editing.

Spec ``docs/specs/2026-11-bot-look.md`` («Живая карточка», «Карточка уехала
вверх»). A flow (Draft edit now, manual input next) keeps the card ``Message``
and calls:

- ``show(card, text, markup)`` on every step: edits the card in place, or, in
  a private chat where the bot has sent something below it since, deletes it
  and sends it again at the bottom. Returns the card to keep (maybe a new one).
- ``drop_reply(message)`` for the person's answer to a step (accepted or not):
  deleted in a private chat, kept in a group chat (no admin rights there).
- ``remove(card)`` when the flow is left without a result («🏠 В начало»,
  ``/cancel``): the card goes from the chat.

A conversation flow (manual input, a ⚡ Preset amount) keeps its card in
``user_data[KEY_CARD]``; ``navigation.leave_flow`` removes a card still there.

The bot remembers its own recent message ids per chat (``SentLog`` on
``TrackingBot``); the person's messages do not count. After a restart the log
is empty and every card stays where it is.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from telegram import InlineKeyboardMarkup, Message
from telegram.constants import ChatType
from telegram.error import BadRequest, TelegramError
from telegram.ext import ExtBot
from telegram.request import HTTPXRequest

logger = logging.getLogger(__name__)

# Bot message ids kept per chat; older ones can no longer be «the last».
SENT_IDS_KEPT = 20
# Same pool size ApplicationBuilder gives its own bot (users run in parallel).
CONNECTION_POOL_SIZE = 256
# Bot API endpoints that put a new message in the chat (edits do not move it).
SEND_PREFIX = "send"
# Telegram's answer to an edit that changes nothing (same text, same buttons).
NOT_MODIFIED = "message is not modified"
# user_data key: the open conversation flow's card (Message) until it shows a result.
KEY_CARD = "live_card"


class SentLog:
    """Ids of the bot's own recent messages per chat, deleted ones dropped."""

    def __init__(self) -> None:
        self._ids: dict[int, list[int]] = {}

    def sent(self, chat_id: int, message_id: int) -> None:
        ids = self._ids.setdefault(chat_id, [])
        ids.append(message_id)
        del ids[:-SENT_IDS_KEPT]

    def deleted(self, chat_id: int, message_id: int) -> None:
        ids = self._ids.get(chat_id, [])
        if message_id in ids:
            ids.remove(message_id)

    def last(self, chat_id: int) -> Optional[int]:
        """The bot's newest message still in the chat; None if nothing is known."""
        ids = self._ids.get(chat_id)
        return max(ids) if ids else None


class TrackingBot(ExtBot):
    """ExtBot that writes every message it sends or deletes into ``sent_log``."""

    def __init__(self, token: str, **kwargs: Any) -> None:
        kwargs.setdefault("request", HTTPXRequest(connection_pool_size=CONNECTION_POOL_SIZE))
        super().__init__(token, **kwargs)
        self._sent_log = SentLog()  # a public attribute cannot be set on a frozen Bot

    @property
    def sent_log(self) -> SentLog:
        return self._sent_log

    async def _send_message(self, endpoint: str, data: Any, *args: Any, **kwargs: Any) -> Any:
        result = await super()._send_message(endpoint, data, *args, **kwargs)
        if endpoint.startswith(SEND_PREFIX) and isinstance(result, Message):
            self._sent_log.sent(result.chat_id, result.message_id)
        return result

    async def delete_message(self, chat_id: Any, message_id: int,  # type: ignore[override]
                             *args: Any, **kwargs: Any) -> bool:
        done = await super().delete_message(chat_id, message_id, *args, **kwargs)
        if isinstance(chat_id, int):
            self._sent_log.deleted(chat_id, message_id)
        return done


def is_private(message: Message) -> bool:
    return message.chat.type == ChatType.PRIVATE


def _moved_up(card: Message) -> bool:
    """True when the bot has sent a message below the card in a private chat."""
    if not is_private(card):
        return False
    log: Optional[SentLog] = getattr(card.get_bot(), "sent_log", None)
    last = log.last(card.chat_id) if log is not None else None
    return last is not None and last != card.message_id


async def _delete(message: Message) -> None:
    try:
        await message.delete()
    except TelegramError as e:  # too old or already gone: the chat is just less tidy
        logger.info("Could not delete message %s: %s", message.message_id, e)


async def remove(card: Message) -> None:
    """Delete a card left without a result (in any chat: it is the bot's own message)."""
    await _delete(card)


async def drop_reply(message: Message) -> None:
    """Delete the person's answer to a card step in a private chat; keep it elsewhere."""
    if is_private(message):
        await _delete(message)


async def show(card: Message, text: str, markup: Optional[InlineKeyboardMarkup] = None,
               *, markup_only: bool = False) -> Message:
    """
    Put a step on the live card: edit it, or send it again at the bottom.

    Parameters
    ----------
    card : Message
        The card as last shown.
    text : str
        Full card text (HTML). Sent as is when the card moves.
    markup : InlineKeyboardMarkup, optional
        Buttons of this step; None removes them.
    markup_only : bool
        Only the buttons changed: an in-place edit keeps the text.

    Returns
    -------
    Message
        The card to keep for the next step: a new message when it moved.
    """
    if _moved_up(card):
        new = await card.get_bot().send_message(card.chat_id, text, reply_markup=markup)
        await _delete(card)
        return new
    try:
        if markup_only:
            await card.edit_reply_markup(reply_markup=markup)
        else:
            await card.edit_text(text, reply_markup=markup)
    except BadRequest as e:  # the same wrong answer twice gives the same card
        if NOT_MODIFIED not in str(e).lower():
            raise
    return card
