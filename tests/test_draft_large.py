"""Large expense on the Draft card (issue #86): flag on creation, «🔶 Крупная», save."""
from __future__ import annotations

import asyncio
from typing import Any, Optional

import pytest

from family_bot import bot
from family_bot import draft_expense as draft
from family_bot.sheet_data import parse_rows
from tests.conftest import FakeSheetsClient, bot_context
from tests.known_bugs.fakes import FakeMessage, FakeQuery, make_update

HEADERS = ["Date", "Month", "Category", "Amount", "Currency", "Who", "Comment"]
LARGE_LABEL = "🔶 Крупная"


def run(coro: Any) -> Any:
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def eur_rate(src: str, dst: str) -> Optional[float]:
    """1 € = 120 дин; nothing else is known."""
    return {("дин", "€"): 1 / 120, ("€", "дин"): 120.0}.get((src, dst))


def _item(**overrides: Any) -> dict:
    base = {"date": "19.11.2026", "category": "Trips", "amount": 1500.0, "currency": "€",
            "who": "Ann", "comment": "билеты"}
    base.update(overrides)
    return base


def _session(*items: dict) -> draft.DraftSession:
    return draft.DraftSession(id="ab12", transcript="билеты", items=list(items))


def _labels(grid: draft.ButtonGrid) -> dict[str, str]:
    return {label: data for row in grid for label, data in row}


def _tap(session: draft.DraftSession, payload: str,
         sheets: Optional[FakeSheetsClient] = None) -> draft.CallbackOutcome:
    return draft.apply_callback(payload, session, ["Food", "Trips"], ["дин", "€"],
                                sheets or FakeSheetsClient())


# --- Flag on creation ---

@pytest.fixture
def ctx(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(bot.fx, "rate", eur_rate)
    return bot_context(categories=["Food", "Trips"], currencies=["дин", "€"],
                       default_currency="€", sheets=FakeSheetsClient())


def test_new_draft_marks_items_above_threshold(ctx: Any) -> None:
    session = _session(_item(amount=1500.0), _item(amount=150_000.0, currency="дин"),
                       _item(amount=500.0), _item(amount=1000.0), _item(currency="GEL"))
    status = FakeMessage()
    run(bot._show_draft(status, ctx, session))
    # Above 1000 € (in € or converted), not equal, not below, no rate → no mark.
    assert [it["large"] for it in session.items] == [True, True, False, False, False]
    assert "🔶" in status.edits[0][0]


def test_new_draft_uses_threshold_setting(ctx: Any) -> None:
    from tests.conftest import settings_of

    settings_of(ctx).large_threshold = 400.0
    session = _session(_item(amount=500.0))
    run(bot._show_draft(FakeMessage(), ctx, session))
    assert session.items[0]["large"] is True


def test_mark_large_sets_every_item() -> None:
    session = _session(_item(amount=5.0), _item(amount=2000.0))
    draft.mark_large(session, lambda amount, cur: amount > 1000)
    assert [it["large"] for it in session.items] == [False, True]


# --- Card text ---

def test_single_card_shows_mark_only_when_large() -> None:
    assert "🔶" not in draft.card_text(_session(_item(large=False)))
    assert "• 🔶 Крупная трата" in draft.card_text(_session(_item(large=True)))


def test_multi_card_marks_the_large_line() -> None:
    text = draft.card_text(_session(_item(comment="кофе", amount=3.0, large=False),
                                    _item(large=True)))
    lines = text.splitlines()
    assert any(line.startswith("1. кофе") for line in lines)
    assert any(line.startswith("2. 🔶 билеты") for line in lines)


def test_item_without_flag_is_not_large() -> None:
    assert "🔶" not in draft.card_text(_session(_item()))


# --- «🔶 Крупная» button ---

def test_single_card_and_item_edit_have_large_button() -> None:
    single = _session(_item())
    assert _labels(draft.main_buttons(single))[LARGE_LABEL] == "vx:ab12:large:0"
    multi = _session(_item(), _item())
    assert LARGE_LABEL not in _labels(draft.main_buttons(multi))
    assert _labels(draft.item_edit_buttons(multi, 1))[LARGE_LABEL] == "vx:ab12:large:1"


def test_large_callback_fits_telegram_limit() -> None:
    session = _session(*[_item() for _ in range(100)])
    data = _labels(draft.item_edit_buttons(session, 99))[LARGE_LABEL]
    assert len(data.encode()) <= draft.CALLBACK_DATA_MAX_BYTES


def test_button_toggles_mark_on_and_off() -> None:
    session = _session(_item(large=True))
    outcome = _tap(session, "large:0")
    assert session.items[0]["large"] is False
    assert "🔶" not in (outcome.text or "")
    assert outcome.buttons == draft.main_buttons(session)
    _tap(session, "large:0")
    assert session.items[0]["large"] is True


def test_button_marks_item_below_threshold() -> None:
    session = _session(_item(comment="кофе", amount=3.0), _item(amount=600.0, large=False))
    outcome = _tap(session, "large:1")
    assert session.items[1]["large"] is True
    assert "2. 🔶 билеты" in (outcome.text or "")


def test_button_on_closed_draft_does_nothing() -> None:
    session = _session(_item(large=False))
    _tap(session, "cancel")
    assert _tap(session, "large:0") == draft.CallbackOutcome()
    assert session.items[0]["large"] is False


def test_old_card_after_restart_says_draft_is_stale() -> None:
    ctx = bot_context(sheets=FakeSheetsClient())  # no Drafts: memory was wiped
    query = FakeQuery("vx:ab12:large:0")
    run(bot.vx_callback(make_update(query=query), ctx))
    assert query.answers == [(draft.STALE_TEXT,)]


# --- Save ---

def test_save_writes_mark_into_large_column() -> None:
    sheets = FakeSheetsClient(data_values=[[*HEADERS, "Large"]])
    session = _session(_item(large=True), _item(comment="кофе", amount=3.0, large=False))
    outcome = _tap(session, "save", sheets)
    assert [r.large for r in outcome.saved] == [True, False]
    values = sheets.read_data_values()
    assert [row[-1] for row in values[1:]] == ["да", ""]
    assert [r.large for r in parse_rows(values[0], values[1:])] == [True, False]


def test_save_adds_large_column_on_first_mark() -> None:
    sheets = FakeSheetsClient(data_values=[HEADERS])
    _tap(_session(_item(large=True)), "save", sheets)
    assert sheets.read_data_headers() == [*HEADERS, "Large"]


def test_save_without_flag_writes_unmarked() -> None:
    sheets = FakeSheetsClient(data_values=[HEADERS])
    records = draft.save_drafts(sheets, [_item()])
    assert records[0].large is False
    assert sheets.read_data_headers() == HEADERS


def test_dinar_base_does_not_mark_everyday_items(monkeypatch: pytest.MonkeyPatch) -> None:
    # Bug: with BASE_CURRENCY=RSD the threshold became 1000 дин, so a voice
    # «кофе 1500 дин» card showed «🔶 Крупная трата».
    monkeypatch.setattr(bot.fx, "rate", eur_rate)
    ctx = bot_context(categories=["Food", "Trips"], currencies=["дин", "€"],
                      default_currency="дин", sheets=FakeSheetsClient())
    session = _session(_item(amount=1500.0, currency="дин"),
                       _item(amount=150_000.0, currency="дин"))
    run(bot._show_draft(FakeMessage(), ctx, session))
    assert [it["large"] for it in session.items] == [False, True]
