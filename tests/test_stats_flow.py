"""StatsFlow seam: query state, conversion details, ConversationHandler states."""
from __future__ import annotations

import asyncio
from datetime import date, datetime
from types import SimpleNamespace
from typing import Any

from telegram import Chat as Chat_, Message, Update
from telegram.ext import CallbackQueryHandler, MessageHandler

from family_bot import conversation_states as states
from family_bot import navigation as nav
from family_bot.flows import stats
from family_bot.sheet_data import Row
from tests.conftest import TEST_CURRENCY_CODES, FakeSheetsClient
from tests.known_bugs.fakes import FakeBot, FakeQuery, make_update

ROWS = [
    Row(date=date(2026, 10, 5), month="2026-10", category="Food", amount=100.0,
    currency="дин", who="Azat", comment=""),
    Row(date=date(2026, 10, 6), month="2026-10", category="Transport", amount=10.0,
    currency="€", who="Azat", comment=""),
]


def run(coro: Any) -> Any:
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def make_flow(sheets: Any = None, get_rate: Any = None) -> stats.StatsFlow:
    return stats.StatsFlow(
        categories=lambda _ctx: ["Food", "Transport"],
        load_rows=lambda _ctx: list(ROWS),
        sheets=lambda _ctx: sheets,
        who=lambda _upd, _ctx: "Azat",
        all_codes=lambda: list(TEST_CURRENCY_CODES),
        get_rate=get_rate or (lambda src, dst: 1.0 if src == dst else 117.0),
    )


def replies(update: Any) -> list[str]:
    return [t for t, _ in update.message.replies]


class Chat:
    """
    One User's private chat with the flow: menu tap, button taps, typed text.

    Every bot message and every typed message is kept, so a test can check
    what stays in the chat at the end.
    """

    def __init__(self, flow: stats.StatsFlow, ctx: Any = None) -> None:
        self.flow = flow
        self.ctx = ctx or SimpleNamespace(user_data={})
        self.bot = FakeBot()  # one chat: shared message ids, every bot message
        self.typed: list[Any] = []  # the User's messages
        self.card: Any = None  # the questions message of the open request
        self.state: Any = None

    def open(self) -> "Chat":
        upd = make_update(nav.SHOW_STATS, bot=self.bot)
        self.typed.append(upd.message)
        self.state = run(self.flow.begin(upd, self.ctx))
        self.card = self.bot.sent[-1]
        return self

    def tap(self, handler: str, data: str) -> Any:
        query = FakeQuery(data, message=self.card)
        self.state = run(getattr(self.flow, handler)(make_update(query=query), self.ctx))
        return query

    def type(self, handler: str, text: str) -> Any:
        upd = make_update(text, bot=self.bot)
        self.typed.append(upd.message)
        self.state = run(getattr(self.flow, handler)(upd, self.ctx))
        return upd

    def question(self) -> str:
        return self.card.edits[-1][0] if self.card.edits else self.card.text

    def buttons(self) -> dict[str, str]:
        kw = self.card.edits[-1][1] if self.card.edits else self.card.kw
        return {b.text: b.callback_data for row in kw["reply_markup"].inline_keyboard
                for b in row}

    def answer(self) -> str:
        (msg,) = [m for m in self.bot.sent if m is not self.card]
        return msg.text

    def left_in_chat(self) -> list[str]:
        """Texts of bot messages and typed answers not deleted (menu tap left out)."""
        messages = [*self.bot.sent, *self.typed[1:]]
        return [m.text for m in messages if not m.deleted]


def cb(payload: str) -> str:
    return stats.CALLBACK + payload


def test_states_cover_every_stat_state() -> None:
    expected = {s for s in states.State if s.name.startswith("STAT_")}
    assert set(make_flow().states()) == expected


def test_specific_category_starts_fresh_query() -> None:
    chat = Chat(make_flow())
    chat.ctx.user_data[stats.QUERY_KEY] = stats.StatQuery(month="2026-09", convert_to="€")
    chat.open()
    chat.tap("on_category", cb(stats.ONE))
    assert chat.state == states.State.STAT_CAT
    assert chat.question() == stats.ASK_CATEGORY
    chat.tap("on_category", chat.buttons()["Food"])
    assert chat.state == states.State.STAT_TYPE
    assert chat.ctx.user_data[stats.QUERY_KEY] == stats.StatQuery(category="Food")


def test_custom_period_only_the_answer_stays_in_chat() -> None:
    chat = Chat(make_flow()).open()
    assert chat.question() == stats.ASK_SCOPE
    chat.tap("on_category", cb(stats.ALL))
    chat.tap("on_type", cb(stats.PERIOD))
    assert chat.question() == stats.ASK_DATE_FROM
    chat.type("on_date_from", "никогда")
    assert chat.question() == f"{stats.BAD_DATE}\n{stats.ASK_DATE_FROM}"
    chat.type("on_date_from", "01.10.2026")
    chat.type("on_date_to", "31.10.2026")
    assert chat.question() == stats.ASK_GROUPING
    chat.tap("on_group_currency", cb(stats.NO))
    chat.type("on_convert_currency", "дин")
    assert chat.state == states.State.CHOOSE_ACTION
    # Every question was an edit of one message; nothing else was sent.
    assert len(chat.bot.sent) == 2  # the questions message and the answer
    answer = chat.answer()
    assert chat.left_in_chat() == [answer]
    assert answer.startswith(
        "📊 <b>Траты с 01.10.2026 по 31.10.2026 · в дин</b>\n\n💰 Итого <b>1 270 дин</b>")
    assert "<b>Transport</b> 1 170\n█████████░ 92%" in answer
    assert "<blockquote expandable>👤 По людям\nAzat — 1 270 дин" in answer
    assert "💱 Курс\n10 € → 1 170 дин (курс 117)</blockquote>" in answer
    assert stats.QUERY_KEY not in chat.ctx.user_data


def test_month_answer_is_month_summary_look() -> None:
    chat = Chat(make_flow()).open()
    chat.tap("on_category", cb(stats.ALL))
    chat.tap("on_type", cb(stats.MONTHS))
    assert chat.question() == stats.ASK_MONTH
    chat.tap("on_month", chat.buttons()["Октябрь 2026"])
    chat.tap("on_group_currency", cb(stats.YES))
    assert chat.state == states.State.CHOOSE_ACTION
    answer = chat.answer()
    assert chat.left_in_chat() == [answer]
    assert answer.startswith("📊 <b>Траты за октябрь 2026</b>\n\n💰 Итого <b>100 дин</b>")
    assert "💰 Итого <b>10 €</b>" in answer
    assert "Azat — 100 дин · 10 €" in answer


def test_last_records_lines_and_answer_alone() -> None:
    chat = Chat(make_flow()).open()
    chat.tap("on_category", cb(stats.ALL))
    chat.tap("on_type", cb(stats.LAST))
    assert chat.state == states.State.CHOOSE_ACTION
    assert chat.left_in_chat() == [
        "📜 <b>Последние записи</b>\n"
        "<b>Transport — 10 €</b> · 06.10 · Azat\n"
        "<b>Food — 100 дин</b> · 05.10 · Azat"]


def test_home_button_removes_the_questions() -> None:
    chat = Chat(make_flow()).open()
    chat.tap("on_category", cb(stats.ALL))
    chat.tap("on_home", nav.HOME_CALLBACK)
    assert chat.state == states.State.CHOOSE_ACTION
    assert chat.left_in_chat() == []
    assert stats.QUERY_KEY not in chat.ctx.user_data


def test_menu_tap_mid_questions_closes_them_and_runs_the_menu() -> None:
    seen: list[str] = []

    async def menu(update: Any, _ctx: Any) -> int:
        seen.append(update.message.text)
        return states.State.CHOOSE_ACTION

    flow = make_flow()
    chat = Chat(flow).open()
    chat.tap("on_category", cb(stats.ALL))
    chat.tap("on_type", cb(stats.PERIOD))
    typed_state = flow.states(menu=menu)[states.State.STAT_DATE_FROM]
    upd = make_update(nav.ADD_EXPENSE)
    handler = next(h for h in typed_state
                   if isinstance(h, MessageHandler) and h.filters.check_update(_tg_update(upd)))
    assert run(handler.callback(upd, chat.ctx)) == states.State.CHOOSE_ACTION
    assert seen == [nav.ADD_EXPENSE] and chat.card.deleted


def _tg_update(upd: Any) -> Update:
    """Real Update for filter checks (the fakes are not Telegram objects)."""
    chat = Chat_(id=1, type="private")
    msg = Message(message_id=1, date=datetime(2026, 11, 23), chat=chat, text=upd.message.text)
    return Update(update_id=1, message=msg)


def test_last_records_error_hides_raw_text() -> None:
    def broken_rows(_ctx: object) -> list[Row]:
        raise RuntimeError("APIError: [503] secret details")

    flow = stats.StatsFlow(
        categories=lambda _ctx: ["Food"],
        load_rows=broken_rows,
        sheets=lambda _ctx: None,
        who=lambda _upd, _ctx: "Azat",
        all_codes=lambda: list(TEST_CURRENCY_CODES),
        get_rate=lambda src, dst: 1.0,
    )
    chat = Chat(flow).open()
    chat.tap("on_category", cb(stats.ALL))
    chat.tap("on_type", cb(stats.LAST))
    text = chat.answer()
    assert text.startswith("❌") and "503" not in text, text


def test_this_month_one_tap_summary(monkeypatch: Any) -> None:
    monkeypatch.setattr(stats.clock, "now", lambda: datetime(2026, 10, 19, 23, 30))
    flow, ctx = make_flow(), SimpleNamespace(user_data={})
    upd = make_update(nav.THIS_MONTH)
    assert run(flow.this_month(upd, ctx)) == states.State.CHOOSE_ACTION
    (text,) = replies(upd)
    assert text.startswith("📅 <b>Итог за октябрь 2026</b>\n\n💰 Итого <b>100 дин</b>"), text
    assert "💰 Итого <b>10 €</b>" in text and "<b>Food</b> 100" in text
    assert "Azat — 100 дин · 10 €" in text


def test_this_month_error_hides_raw_text() -> None:
    def broken_rows(_ctx: object) -> list[Row]:
        raise RuntimeError("APIError: [503] secret details")

    flow = stats.StatsFlow(
        categories=lambda _ctx: ["Food"],
        load_rows=broken_rows,
        sheets=lambda _ctx: None,
        who=lambda _upd, _ctx: "Azat",
        all_codes=lambda: list(TEST_CURRENCY_CODES),
        get_rate=lambda src, dst: 1.0,
    )
    upd = make_update(nav.THIS_MONTH)
    assert run(flow.this_month(upd, SimpleNamespace(user_data={}))) == states.State.CHOOSE_ACTION
    text = replies(upd)[0]
    assert text.startswith("❌") and "503" not in text, text


# --- Conversion target: the same Currency picker as manual input (issue #34) ---

def _asked_for_target(sheets: Any = None, get_rate: Any = None) -> Chat:
    chat = Chat(make_flow(sheets, get_rate)).open()
    chat.tap("on_category", cb(stats.ALL))
    chat.tap("on_type", cb(stats.MONTHS))
    chat.tap("on_month", cb(stats.MONTH + "2026-10"))
    chat.tap("on_group_currency", cb(stats.NO))
    assert chat.state == states.State.STAT_CONVERT_CURRENCY
    assert chat.question() == stats.ASK_TARGET
    return chat


def test_target_question_shows_recent_currencies_and_other() -> None:
    sheets = FakeSheetsClient()
    sheets.users[111] = ["CNY", "дин", "€"]
    chat = _asked_for_target(sheets)
    markup = chat.card.edits[-1][1]["reply_markup"]
    rows = [[b.text for b in row] for row in markup.inline_keyboard]
    assert rows[0] == ["🇨🇳 CNY", "🇷🇸 дин", "🇪🇺 €"]
    assert rows[1] == ["🌍 Другая"]
    assert chat.buttons()["🇨🇳 CNY"] == "scur:pick:CNY"


def test_other_page_then_pick_converts_into_any_currency() -> None:
    sheets = FakeSheetsClient()
    chat = _asked_for_target(sheets)
    chat.tap("on_convert_button", "scur:page:0")
    assert chat.state == states.State.STAT_CONVERT_CURRENCY
    assert chat.question().startswith("🌍 Все валюты")
    chat.tap("on_convert_button", chat.buttons()["🇬🇪 GEL"])
    assert chat.state == states.State.CHOOSE_ACTION
    result = chat.answer()
    assert "· в GEL</b>" in result and "💰 Итого <b>12 870 GEL</b>" in result, result
    assert chat.left_in_chat() == [result]
    assert chat.ctx.user_data["recent_currencies"][0] == "GEL"
    assert sheets.users[111][0] == "GEL"


def test_typed_name_picks_the_target() -> None:
    chat = _asked_for_target()
    upd = chat.type("on_convert_currency", "юань")
    assert chat.state == states.State.CHOOSE_ACTION
    assert upd.message.deleted
    assert "· в CNY</b>" in chat.answer()


def test_typed_text_with_several_matches_offers_buttons() -> None:
    chat = _asked_for_target()
    chat.type("on_convert_currency", "долл")
    assert chat.state == states.State.STAT_CONVERT_CURRENCY
    assert {"🇨🇦 CAD", "🇺🇸 $"} <= set(chat.buttons())


def test_unknown_typed_currency_asks_again() -> None:
    chat = _asked_for_target()
    upd = chat.type("on_convert_currency", "xyz")
    assert chat.state == states.State.STAT_CONVERT_CURRENCY
    assert chat.question().startswith("❌") and "🌍 Другая" in chat.buttons()
    assert upd.message.deleted


def test_missing_rate_is_said_plainly() -> None:
    chat = _asked_for_target(get_rate=lambda src, dst: 1.0 if src == dst else None)
    chat.tap("on_convert_button", "scur:pick:RSD")
    assert chat.state == states.State.CHOOSE_ACTION
    result = chat.answer()
    assert "Нет курса для пересчёта в дин: €" in result, result
    assert "Итого" not in result


def test_picker_buttons_have_a_handler_in_target_state() -> None:
    patterns = [h.pattern for h in make_flow().states()[states.State.STAT_CONVERT_CURRENCY]
                if isinstance(h, CallbackQueryHandler)]
    assert any(p.match("scur:pick:RSD") for p in patterns)
    assert any(p.match("scur:page:2") for p in patterns)


# Large expense (issue #87).
LARGE_ROW = Row(date=date(2026, 10, 7), month="2026-10", category="Travel", amount=2000.0,
                currency="€", who="Azat", comment="tickets", large=True)


def large_flow(rows: list[Row]) -> stats.StatsFlow:
    flow = make_flow()
    flow._load_rows = lambda _ctx: list(rows)
    return flow


def test_category_step_offers_only_large() -> None:
    chat = Chat(make_flow()).open()
    assert chat.buttons()[stats.ONLY_LARGE_BUTTON] == cb(stats.LARGE)


def _month_answer(chat: Chat, scope: str, group: str) -> None:
    chat.tap("on_category", cb(scope))
    chat.tap("on_type", cb(stats.MONTHS))
    chat.tap("on_month", cb(stats.MONTH + "2026-10"))
    chat.tap("on_group_currency", cb(group))


def test_default_stats_leave_large_out() -> None:
    chat = Chat(large_flow([*ROWS, LARGE_ROW])).open()
    _month_answer(chat, stats.ALL, stats.YES)
    answer = chat.answer()
    assert "💰 Итого <b>10 €</b>" in answer
    assert "🔶 Крупные (не в итоге)\ntickets — 2 000 €" in answer


def test_only_large_same_path_and_conversion() -> None:
    chat = Chat(large_flow([*ROWS, LARGE_ROW])).open()
    chat.tap("on_category", cb(stats.LARGE))
    assert chat.state == states.State.STAT_TYPE
    assert "только крупные траты" in chat.question()
    assert chat.ctx.user_data[stats.QUERY_KEY] == stats.StatQuery(only_large=True)
    chat.tap("on_type", cb(stats.MONTHS))
    chat.tap("on_month", cb(stats.MONTH + "2026-10"))
    chat.tap("on_group_currency", cb(stats.NO))
    chat.type("on_convert_currency", "дин")
    answer = chat.answer()
    assert answer.startswith(
        "🔶 <b>Крупные траты за октябрь 2026 · в дин</b>\n\n💰 Итого <b>234 000 дин</b>")
    assert "<b>Travel</b> 234 000" in answer


def test_only_large_last_records() -> None:
    chat = Chat(large_flow([*ROWS, LARGE_ROW])).open()
    chat.tap("on_category", cb(stats.LARGE))
    chat.tap("on_type", cb(stats.LAST))
    assert chat.answer() == (
        "🔶 <b>Последние крупные траты</b>\n<b>tickets — 2 000 €</b> · Travel · 07.10 · Azat")


def test_only_large_without_marked_rows_says_so() -> None:
    chat = Chat(large_flow(list(ROWS))).open()
    _month_answer(chat, stats.LARGE, stats.YES)
    assert chat.answer() == (
        "🔶 <b>Крупные траты за октябрь 2026</b>\n\n🔶 Крупных трат за этот период нет")


def test_new_query_does_not_inherit_only_large() -> None:
    chat = Chat(make_flow()).open()
    chat.tap("on_category", cb(stats.LARGE))
    chat.open()
    assert chat.ctx.user_data[stats.QUERY_KEY].only_large is False
    chat.tap("on_category", cb(stats.ALL))
    assert chat.ctx.user_data[stats.QUERY_KEY] == stats.StatQuery()
