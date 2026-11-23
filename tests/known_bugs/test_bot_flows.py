"""Known-bug reproductions for bot flows.

Each test asserts the correct behaviour and is marked xfail(strict=True) with
its finding id from docs/specs/2026-11-revision.md. A fix removes the marker.
"""
import asyncio
import math
import threading
import time
from types import SimpleNamespace

import pytest
from telegram import Chat, Message, Update, User
from datetime import date, datetime, timezone

from family_bot import bot
from family_bot import draft_expense as draft
from family_bot import navigation as nav
from family_bot.flows import stats as flow_stats
from tests.conftest import settings_of
from tests.known_bugs.fakes import FakeQuery, FakeSheets, known_bug, make_update
from family_bot.conversation_states import State
from family_bot.sheet_data import Row

CHOOSE_ACTION, STAT_SHOW_DETAILS = State.CHOOSE_ACTION, State.STAT_SHOW_DETAILS

ROWS = [
    Row(date=date(2026, 10, 5), month="2026-10", category="Food", amount=100.0,
     currency="дин", who="Ann", comment=""),
    Row(date=date(2026, 11, 5), month="2026-11", category="Food", amount=7.0,
     currency="дин", who="Ann", comment=""),
]


def run(coro):
    # A private loop: asyncio.run() would unset the main-thread loop that
    # tests/test_navigation.py still reaches via asyncio.get_event_loop().
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture(autouse=True)
def _rows(monkeypatch):
    monkeypatch.setattr(bot, "load_expense_rows", lambda sheets: list(ROWS))


def last_reply_texts(update):
    return [t for t, _ in update.message.replies]


# 1. Stats "Yes" path left the month behind → a later custom period showed the old month.
def test_custom_period_after_month_flow_uses_requested_period(ctx):
    run(bot.choose_action(make_update(nav.SHOW_STATS), ctx))
    run(bot.STATS.on_category(make_update(flow_stats.ALL_BUTTON), ctx))
    run(bot.STATS.on_type(make_update(flow_stats.MONTHS_BUTTON), ctx))
    run(bot.STATS.on_month(make_update("2026-10"), ctx))
    assert run(bot.STATS.on_group_currency(make_update(flow_stats.YES_BUTTON), ctx)) == CHOOSE_ACTION
    # second query from the main menu: custom period in November
    run(bot.choose_action(make_update(nav.SHOW_STATS), ctx))
    run(bot.STATS.on_category(make_update(flow_stats.ALL_BUTTON), ctx))
    run(bot.STATS.on_type(make_update(flow_stats.PERIOD_BUTTON), ctx))
    run(bot.STATS.on_date_from(make_update("01.11.2026"), ctx))
    run(bot.STATS.on_date_to(make_update("30.11.2026"), ctx))
    upd = make_update(flow_stats.YES_BUTTON)
    run(bot.STATS.on_group_currency(upd, ctx))
    result = last_reply_texts(upd)[0]
    assert "с 01.11.2026 по 30.11.2026" in result, result


# 2. /start mid-flow kept a stale conversion currency → next "Yes" query was converted.
def test_start_clears_previous_flow_state(ctx):
    ctx.user_data[flow_stats.QUERY_KEY] = flow_stats.StatQuery(month="2026-10", convert_to="€")
    run(bot.start(make_update("/start"), ctx))
    assert flow_stats.QUERY_KEY not in ctx.user_data, ctx.user_data


# 3. "No" grouping + convert to the only currency (or FX failure) → no details →
#    main menu was shown but state stayed STAT_SHOW_DETAILS → next menu tap was eaten.
def test_no_details_returns_to_menu_state(ctx):
    ctx.user_data[flow_stats.QUERY_KEY] = flow_stats.StatQuery(month="2026-10", group_by_currency=False)
    state = run(bot.STATS.on_convert_currency(make_update("дин"), ctx))
    assert state == CHOOSE_ACTION, f"state={state} (STAT_SHOW_DETAILS={STAT_SHOW_DETAILS})"


def test_menu_tap_after_no_details_is_not_swallowed(ctx):
    ctx.user_data[flow_stats.QUERY_KEY] = flow_stats.StatQuery(month="2026-10", group_by_currency=False)
    state = run(bot.STATS.on_convert_currency(make_update("дин"), ctx))
    handler = {STAT_SHOW_DETAILS: bot.STATS.on_show_details, CHOOSE_ACTION: bot.choose_action}[state]
    upd = make_update(nav.ADD_EXPENSE)
    run(handler(upd, ctx))
    assert "Введите сумму" in last_reply_texts(upd)[0], last_reply_texts(upd)


# 4. Manual amount accepted nan / inf / negative / zero (R6).
@pytest.mark.parametrize("text", ["nan", "inf", "-500", "0"])
def test_type_amount_rejects_non_positive_or_non_finite(ctx, text):
    run(bot.MANUAL.type_amount(make_update(text), ctx))
    assert "amt" not in ctx.user_data, f"{text!r} accepted as {ctx.user_data.get('amt')}"


# 5. "$" must be on the Currency buttons so voice/photo dollars stay dollars (R4).
def test_dollar_currency_survives_snap(ctx: SimpleNamespace) -> None:
    from family_bot import voice_input as vx
    assert vx._snap_currency("$", settings_of(ctx).currencies, settings_of(ctx).default_currency) == "$"


CHIPS = {"comment": "chips", "amount": 100.0, "currency": "дин",
         "date": "01.11.2026", "category": "Food"}


def save_cb(session):
    """callback_data of the card's «✅ Сохранить» button."""
    label, cb, *_ = draft.main_buttons(session)[0][0]
    assert label.startswith("✅")
    return cb


# 6. A second voice draft silently replaced the first; the OLD card's Save saved the NEW items.
def test_old_card_save_does_not_save_other_draft(ctx, monkeypatch):
    a = draft.add_draft(ctx.user_data, draft.session_from_voice(
        [dict(CHIPS)], who="Ann", transcript="chips"))
    draft.add_draft(ctx.user_data, draft.session_from_voice(
        [{"comment": "taxi", "amount": 900.0, "currency": "дин",
          "date": "01.11.2026", "category": "Transport"}],
        who="Ann", transcript="taxi"))
    sheets = FakeSheets()
    settings_of(ctx).sheets = sheets
    run(bot.vx_callback(make_update(query=FakeQuery(save_cb(a))), ctx))  # tap Save on card A
    assert [r[6] for r in sheets.rows] == ["chips"], sheets.rows


# 7. go_home (/cancel, 🏠, finishing any manual/preset/stat flow) wiped an open voice draft.
def test_go_home_keeps_open_draft(ctx):
    a = draft.add_draft(ctx.user_data, draft.session_from_voice(
        [dict(CHIPS)], who="Ann", transcript="chips"))
    ctx.user_data["amt"] = 5.0  # manual-flow key: still cleared
    run(nav.go_home(make_update(nav.HOME_BUTTON), ctx))
    kept = draft.get_draft(ctx.user_data, a.id)
    assert kept is not None and kept.items, "draft lost"
    assert "amt" not in ctx.user_data


# 8. Multi-item save failed half-way → session kept → retry duplicated saved items.
def test_partial_save_retry_does_not_duplicate(ctx, monkeypatch):
    items = [{"comment": f"i{n}", "amount": 10.0, "currency": "дин",
              "date": "01.11.2026", "category": "Food"} for n in range(3)]
    s = draft.add_draft(ctx.user_data, draft.session_from_receipt(items, who="Ann"))
    sheets = FakeSheets(fail_on_call=1)
    settings_of(ctx).sheets = sheets
    with pytest.raises(RuntimeError):
        run(bot.vx_callback(make_update(query=FakeQuery(save_cb(s))), ctx))
    run(bot.vx_callback(make_update(query=FakeQuery(save_cb(s))), ctx))  # user retries
    comments = [r[6] for r in sheets.rows]
    assert comments == ["i0", "i1", "i2"], comments


# 9. Double tap on Save: second callback overwrote "✅ Сохранено" with "Нечего сохранять".
def test_double_tap_save_keeps_success_text(ctx, monkeypatch):
    s = draft.add_draft(ctx.user_data, draft.session_from_voice(
        [dict(CHIPS)], who="Ann", transcript="chips"))
    sheets = FakeSheets()
    settings_of(ctx).sheets = sheets
    q1, q2 = FakeQuery(save_cb(s)), FakeQuery(save_cb(s))
    run(bot.vx_callback(make_update(query=q1), ctx))
    run(bot.vx_callback(make_update(query=q2), ctx))
    assert len(sheets.rows) == 1
    assert q1.edits and q1.edits[-1].startswith("✅"), q1.edits
    assert not q2.edits or q2.edits[-1].startswith("✅"), q2.edits


# 10. Blocking gspread call inside async handler stalls the event loop for everyone.
def test_save_does_not_block_event_loop(ctx, monkeypatch):
    class SlowSheets(FakeSheets):
        def append_data_rows(self, rows):
            time.sleep(0.6)
            super().append_data_rows(rows)

    sheets = SlowSheets()
    settings_of(ctx).sheets = sheets
    s = draft.add_draft(ctx.user_data, draft.session_from_voice(
        [dict(CHIPS)], who="Ann", transcript="chips"))

    async def scenario():
        ticks = 0

        async def ticker():
            nonlocal ticks
            for _ in range(6):
                await asyncio.sleep(0.1)
                ticks += 1

        t = asyncio.create_task(ticker())
        await asyncio.sleep(0)
        await bot.vx_callback(make_update(query=FakeQuery(save_cb(s))), ctx)
        ticked_during_save = ticks
        await t
        return ticked_during_save

    assert run(scenario()) >= 4, "event loop frozen during Sheets write"


# 11. Edited messages were routed into the conversation like new input (R12).
def test_edited_message_not_treated_as_new_input():
    user = User(id=111, first_name="A", is_bot=False)
    chat = Chat(id=111, type="private")
    msg = Message(message_id=5, date=datetime.now(timezone.utc), chat=chat,
                  from_user=user, text="5000")
    upd = Update(update_id=1, edited_message=msg)
    # bot.EDITED_MESSAGES runs in group -2, before every other handler, and stops the update
    assert bot.EDITED_MESSAGES.check_update(upd), "edited '5000' would be accepted as a new amount"


# 12. Category index in callback_data went stale after /reloadcats reordered Config.
def test_setcat_uses_category_shown_on_card():
    old = ["Food", "Transport", "Other"]
    new = ["Transport", "Food", "Other"]  # Config re-sorted, then /reloadcats
    s = draft.DraftSession(id="ab12", items=[{
        "comment": "", "amount": 1.0, "currency": "дин",
        "date": "01.11.2026", "category": "Food", "who": "A"}])
    btn = draft.category_buttons(s, 0, old)
    label, cb = btn[0][1]  # user taps "Transport"
    draft_id, payload = draft.parse_callback(cb)
    assert draft_id == s.id
    draft.apply_callback(payload, s, new, ["дин"], FakeSheets())
    assert s.items[0]["category"] == label, s.items[0]["category"]
