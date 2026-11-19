"""Expense typed as text («кофе 350») → Draft card (F1), Claude stubbed, no network."""
from __future__ import annotations

import asyncio
import json
import sys
from types import SimpleNamespace
from typing import Any, Callable, Coroutine, Optional

import pytest

import bot
import draft_expense as draft
import errors
import text_input
from conversation_states import CHOOSE_ACTION
from tests.conftest import FakeSheetsClient
from tests.known_bugs.fakes import FakeMessage, make_update

CATS = ["Food", "Transport", "Other"]
CURS = ["₽", "дин", "€", "$"]
TODAY = "19.11.2026"


def run(coro: Coroutine[Any, Any, Any]) -> Any:
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class FakeAnthropic:
    """Stands in for anthropic.Anthropic: returns a fixed reply, records prompts."""

    reply: dict = {}
    prompts: list[str] = []

    def __init__(self, *args: Any, **kw: Any) -> None:
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw: Any) -> SimpleNamespace:
        FakeAnthropic.prompts.append(kw["messages"][0]["content"])
        block = SimpleNamespace(type="text", text=json.dumps(FakeAnthropic.reply))
        return SimpleNamespace(content=[block])


@pytest.fixture
def claude(monkeypatch: pytest.MonkeyPatch) -> type[FakeAnthropic]:
    """Stub Claude: set FakeAnthropic.reply in the test."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(Anthropic=FakeAnthropic))
    FakeAnthropic.reply = {"expenses": [], "note": ""}
    FakeAnthropic.prompts = []
    return FakeAnthropic


def _coffee(**overrides: Any) -> dict:
    item = {"comment": "кофе", "amount": 350, "currency": "дин",
            "date": TODAY, "category": "Food"}
    item.update(overrides)
    return {"expenses": [item], "note": ""}


def _parse(text: str) -> Optional[draft.DraftSession]:
    return text_input.draft_from_text(
        text, categories=CATS, currencies=CURS, today=TODAY, who="Azat",
        default_currency="дин")


# --- parsing path (text_input + voice_input.extract_expenses) ---

def test_coffee_350_becomes_one_draft_item(claude):
    claude.reply = _coffee()
    session = _parse("кофе 350")
    assert session is not None
    assert session.items == [{"comment": "кофе", "amount": 350.0, "currency": "дин",
                              "date": TODAY, "category": "Food", "who": "Azat"}]
    assert "кофе 350" in claude.prompts[0]
    text = draft.card_text(session)
    assert "«кофе 350»" in text and "Расшифровка" not in text
    assert "350.00 дин" in text


def test_category_and_currency_snap_to_the_lists(claude):
    claude.reply = _coffee(category="food", currency="EUR")
    session = _parse("кофе 3 евро")
    assert session is not None
    assert session.items[0]["category"] == "Food"
    assert session.items[0]["currency"] == "€"


def test_nothing_found_gives_no_draft(claude):
    claude.reply = {"expenses": [], "note": "нет суммы"}
    assert _parse("кофе 350") is None


def test_amount_not_above_zero_gives_no_draft(claude):
    claude.reply = _coffee(amount=-350)
    assert _parse("кофе -350") is None


@pytest.mark.parametrize("text, expected", [
    ("кофе 350", True),
    ("такси 12,5 €", True),
    ("привет", False),
    ("   ", False),
])
def test_only_text_with_a_number_goes_to_claude(text, expected):
    assert text_input.looks_like_expense(text) is expected


# --- main menu handler ---

class StatusMessage(FakeMessage):
    """Message whose replies are kept, so edits of the status reply are visible."""

    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.sent: list[FakeMessage] = []

    async def reply_text(self, text: str, **kw: Any) -> FakeMessage:
        await super().reply_text(text, **kw)
        sent = FakeMessage(text)
        self.sent.append(sent)
        return sent


@pytest.fixture
def menu(monkeypatch: pytest.MonkeyPatch) -> Callable[[str], tuple]:
    monkeypatch.setattr(bot, "CATS", CATS)
    monkeypatch.setattr(bot, "CURS", CURS)

    def send(text: str) -> tuple:
        upd = make_update(text)
        upd.message = upd.effective_message = StatusMessage(text)
        ctx = SimpleNamespace(user_data={}, args=[])
        state = run(bot.choose_action(upd, ctx))
        return state, upd.message, ctx
    return send


def _last_text(msg: StatusMessage) -> tuple[str, dict]:
    sent = msg.sent[-1]
    return sent.edits[-1] if sent.edits else (sent.text or "", {})


def test_menu_text_shows_draft_card_and_saves_with_one_tap(claude, menu):
    claude.reply = _coffee()
    state, msg, ctx = menu("кофе 350")
    assert state == CHOOSE_ACTION
    text, kw = _last_text(msg)
    assert "📝 Черновик траты" in text
    (session,) = ctx.user_data[draft.KEY_DRAFTS].values()
    save_cb = kw["reply_markup"].inline_keyboard[0][0].callback_data
    assert save_cb == f"vx:{session.id}:save"

    sheets = FakeSheetsClient()
    draft_id, payload = draft.parse_callback(save_cb)
    outcome = draft.apply_callback(payload, session, CATS, CURS, sheets)
    assert outcome.text and outcome.text.startswith("✅ Сохранено")
    assert len(sheets.appended_rows) == 1


def test_menu_text_without_number_gets_hint_and_no_claude_call(claude, menu):
    state, msg, ctx = menu("привет")
    assert state == CHOOSE_ACTION
    assert msg.replies[-1][0] == text_input.HINT
    assert claude.prompts == []
    assert draft.KEY_DRAFTS not in ctx.user_data


def test_menu_text_claude_finds_nothing_gets_hint(claude, menu):
    state, msg, ctx = menu("кофе 350")
    assert _last_text(msg)[0] == text_input.HINT
    assert draft.KEY_DRAFTS not in ctx.user_data


def test_menu_text_claude_error_gets_shared_reply(monkeypatch, menu):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)  # extract raises
    state, msg, ctx = menu("кофе 350")
    assert state == CHOOSE_ACTION
    assert _last_text(msg)[0] == errors.NOT_PARSED


def test_menu_buttons_still_work(menu):
    state, msg, _ = menu("📊 Show statistics")
    assert msg.replies and msg.replies[-1][0] != text_input.HINT
