"""Telegram and Sheets fakes for known-bug reproduction tests."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Optional

import pytest

SPEC = "docs/specs/2026-11-revision.md"


def known_bug(finding: str) -> pytest.MarkDecorator:
    """Mark a test that reproduces an open finding from the revision spec.

    strict=True turns an unexpected pass into a failure, so the fix must
    remove the marker in the same change.
    """
    return pytest.mark.xfail(strict=True, reason=f"known bug {finding}, see {SPEC}")


class FakeBot:
    """Chat-wide message ids, the bot's sends and deletions.

    ``sent_log`` is the same record ``live_card.TrackingBot`` keeps, so a test
    can bury a card under newer bot messages.
    """

    def __init__(self) -> None:
        from family_bot.live_card import SentLog

        self.sent_log = SentLog()
        self.last_id = 0
        self.sent: list["FakeMessage"] = []
        self.deleted: list[int] = []  # message ids, in order

    def next_id(self) -> int:
        self.last_id += 1
        return self.last_id

    async def send_message(self, chat_id: int, text: str, **kw: Any) -> "FakeMessage":
        msg = FakeMessage(text, bot=self, chat_id=chat_id, from_bot=True)
        msg.kw = kw
        return msg

    async def delete_message(self, chat_id: int, message_id: int) -> bool:
        self.deleted.append(message_id)
        self.sent_log.deleted(chat_id, message_id)
        return True


class FakeMessage:
    """Message with an id in its chat; bot messages go to the bot's ``sent_log``."""

    def __init__(self, text: Optional[str] = None, *, bot: Optional[FakeBot] = None,
                 chat_id: int = 111, chat_type: str = "private",
                 from_bot: bool = False) -> None:
        self.text = text
        self.bot = bot or FakeBot()
        self.chat_id = chat_id
        self.chat = SimpleNamespace(id=chat_id, type=chat_type)
        self.message_id = self.bot.next_id()
        self.kw: dict[str, Any] = {}
        self.replies: list[tuple[str, dict[str, Any]]] = []
        self.edits: list[tuple[str, dict[str, Any]]] = []
        self.deleted = False
        if from_bot:
            self.bot.sent.append(self)
            self.bot.sent_log.sent(chat_id, self.message_id)

    def get_bot(self) -> FakeBot:
        return self.bot

    async def reply_text(self, text: str, **kw: Any) -> "FakeMessage":
        self.replies.append((text, kw))
        reply = FakeMessage(text, bot=self.bot, chat_id=self.chat_id,
                            chat_type=self.chat.type, from_bot=True)
        reply.kw = kw
        return reply

    async def edit_text(self, text: str, **kw: Any) -> None:
        self.edits.append((text, kw))

    async def edit_reply_markup(self, **kw: Any) -> None:
        self.edits.append(("markup", kw))

    async def delete(self) -> bool:
        self.deleted = True
        return await self.bot.delete_message(self.chat_id, self.message_id)


class FakeQuery:
    def __init__(self, data: str, message: Optional[FakeMessage] = None) -> None:
        self.data = data
        self.message = message or FakeMessage()
        self.edits: list[Any] = []
        self.markups: list[Any] = []  # reply_markup of each edit_message_text
        self.answers: list[tuple[Any, ...]] = []

    async def answer(self, *args: Any, **kw: Any) -> None:
        self.answers.append(args)

    async def edit_message_text(self, text: str, **kw: Any) -> None:
        self.edits.append(text)
        self.markups.append(kw.get("reply_markup"))

    async def edit_message_reply_markup(self, **kw: Any) -> None:
        self.edits.append(("markup", kw))


def make_update(text: Optional[str] = None, user_id: int = 111,
                query: Optional[FakeQuery] = None,
                bot: Optional[FakeBot] = None) -> SimpleNamespace:
    """Update from the person in their private chat (chat id = user id).

    Pass one ``bot`` to several updates to share message ids and the bot's
    ``sent_log`` across them.
    """
    msg = FakeMessage(text, bot=bot, chat_id=user_id) if query is None else None
    effective = msg if msg else query.message
    return SimpleNamespace(
        message=msg,
        effective_message=effective,
        effective_chat=effective.chat,
        effective_user=SimpleNamespace(id=user_id, username="u", first_name="U"),
        callback_query=query,
    )


class FakeSheets:
    """Records Data rows; optionally fails on the N-th append call.

    A failing ``append_data_rows`` writes nothing, like one Sheets API request.

    ``headers`` is the Data header row; empty means a sheet without headers.
    """

    def __init__(self, fail_on_call: Optional[int] = None,
                 headers: Optional[list[str]] = None) -> None:
        self.headers = list(headers or [])
        self.rows: list[list[Any]] = []
        self.calls = 0
        self.fail_on_call = fail_on_call

    def read_data_headers(self) -> list[str]:
        return list(self.headers)

    def append_data_row(self, row: list[Any]) -> None:
        self.calls += 1
        if self.fail_on_call is not None and self.calls == self.fail_on_call:
            raise RuntimeError("APIError: [429] Quota exceeded")
        self.rows.append(row)

    def append_data_rows(self, rows: list[list[Any]]) -> None:
        self.calls += 1
        if self.fail_on_call is not None and self.calls == self.fail_on_call:
            raise RuntimeError("APIError: [429] Quota exceeded")
        self.rows.extend(rows)
