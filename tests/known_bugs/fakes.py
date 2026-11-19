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


class FakeMessage:
    def __init__(self, text: Optional[str] = None) -> None:
        self.text = text
        self.replies: list[tuple[str, dict[str, Any]]] = []
        self.edits: list[tuple[str, dict[str, Any]]] = []

    async def reply_text(self, text: str, **kw: Any) -> "FakeMessage":
        self.replies.append((text, kw))
        return FakeMessage(text)

    async def edit_text(self, text: str, **kw: Any) -> None:
        self.edits.append((text, kw))


class FakeQuery:
    def __init__(self, data: str, message: Optional[FakeMessage] = None) -> None:
        self.data = data
        self.message = message or FakeMessage()
        self.edits: list[Any] = []

    async def answer(self, *args: Any, **kw: Any) -> None:
        pass

    async def edit_message_text(self, text: str, **kw: Any) -> None:
        self.edits.append(text)

    async def edit_message_reply_markup(self, **kw: Any) -> None:
        self.edits.append(("markup", kw))


def make_update(text: Optional[str] = None, user_id: int = 111,
                query: Optional[FakeQuery] = None) -> SimpleNamespace:
    msg = FakeMessage(text) if query is None else None
    return SimpleNamespace(
        message=msg,
        effective_message=msg if msg else query.message,
        effective_user=SimpleNamespace(id=user_id, username="u", first_name="U"),
        callback_query=query,
    )


class FakeSheets:
    """Records Data rows; optionally fails on the N-th append.

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
