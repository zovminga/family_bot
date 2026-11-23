"""The bot speaks Russian (issue #29, U1): walk every flow, collect what the
User sees (message texts, edited texts, button labels, command menu) and check
there is no English word in it.

Category and Spender names come from the sheet as they are, so the test data
uses Russian ones. Technical names the User must type or look for stay Latin
(commands, ``ALLOWED_USERS``, ``.env``, Telegram, Google Sheets, sheet names, ``ID``,
ISO currency codes such as ``CNY`` and ``GEL`` — owner decision Q12).
"""
from __future__ import annotations

import asyncio
import re
from datetime import date, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from telegram import InlineKeyboardMarkup, ReplyKeyboardMarkup

from family_bot import analytics
from family_bot import bot
from family_bot import draft_expense as draft
from family_bot import errors
from family_bot import flow_preset
from family_bot import navigation as nav
from family_bot import text_input
from family_bot.flows import manual
from family_bot.flows import stats as flow_stats
from family_bot.sheet_data import Row
from tests.conftest import FakeSheetsClient, bot_context
from tests.known_bugs.fakes import FakeQuery, make_update

CATEGORIES = ["Еда", "Транспорт"]
ROWS = [
    Row(date=date(2026, 10, 5), month="2026-10", category="Еда", amount=1234.5,
        currency="дин", who="Анна", comment="кофе"),
    Row(date=date(2026, 10, 6), month="2026-10", category="Транспорт", amount=10.0,
        currency="€", who="Анна", comment=""),
]
PRESETS = [{"name": "Аренда", "amount": 500.0, "currency": "€", "category": "Еда",
            "who": "Анна"},
           {"name": "Коммуналка", "amount": None, "currency": "дин", "category": "Еда",
            "who": "Анна"}]
# Latin words the User is meant to see: commands, settings, services, sheet names,
# ISO currency codes.
ALLOWED_LATIN = re.compile(
    r"/\w+|ALLOWED_USERS|\.env|Telegram|Google|Sheets|Config|Regular_expenses|\bID\b"
    r"|\b[A-Z]{3}\b")
LATIN_WORD = re.compile(r"[A-Za-z]{2,}")
# Telegram HTML tags (parse mode HTML): the User sees the text, not the tags.
HTML_TAG = re.compile(r"</?[a-z-]+(?: [^>]*)?>")


def run(coro: Any) -> Any:
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def english_words(text: str) -> list[str]:
    """Latin words the User sees, after removing tags and the allowed technical names."""
    return LATIN_WORD.findall(ALLOWED_LATIN.sub("", HTML_TAG.sub("", text)))


def markup_labels(markup: Any) -> list[str]:
    """Button labels of a reply or inline keyboard; empty for anything else."""
    if isinstance(markup, ReplyKeyboardMarkup):
        return [b.text for row in markup.keyboard for b in row]
    if isinstance(markup, InlineKeyboardMarkup):
        return [b.text for row in markup.inline_keyboard for b in row]
    return []


class Seen:
    """Every text and button label sent through the fake updates."""

    def __init__(self) -> None:
        self.texts: list[str] = []
        self.updates: list[SimpleNamespace] = []

    def update(self, text: str | None = None, query: FakeQuery | None = None,
               user_id: int = 111) -> SimpleNamespace:
        upd = make_update(text, user_id=user_id, query=query)
        self.updates.append(upd)
        return upd

    def collect(self) -> list[str]:
        out = list(self.texts)
        for upd in self.updates:
            messages = [m for m in (upd.message, upd.effective_message) if m is not None]
            for msg in messages:
                for text, kw in [*msg.replies, *msg.edits]:
                    out += [text, *markup_labels(kw.get("reply_markup"))]
            if upd.callback_query is not None:
                out += [e for e in upd.callback_query.edits if isinstance(e, str)]
                for markup in upd.callback_query.markups:
                    out += markup_labels(markup)
        return out


@pytest.fixture
def seen(monkeypatch: pytest.MonkeyPatch) -> Seen:
    monkeypatch.setattr(bot, "load_expense_rows", lambda sheets: list(ROWS))
    monkeypatch.setattr(bot.clock, "now", lambda: datetime(2026, 11, 19))
    return Seen()


@pytest.fixture
def ctx() -> SimpleNamespace:
    return bot_context(categories=list(CATEGORIES), currencies=["дин", "€"],
                       allowed_users={111: "Анна"}, presets=list(PRESETS),
                       sheets=FakeSheetsClient(categories=list(CATEGORIES)))


def assert_russian(texts: list[str]) -> None:
    found = {t: english_words(t) for t in texts if english_words(t)}
    assert not found, found


def test_main_menu_and_commands(seen: Seen, ctx: SimpleNamespace) -> None:
    run(bot.start(seen.update("/start"), ctx))
    for handler in (bot.whoami, bot.allowlist_hint, bot.show_categories,
                    bot.test_connection, bot.reload_cats, bot.cancel):
        run(handler(seen.update("/x"), ctx))
    run(bot.whoami(seen.update("/whoami", user_id=999), ctx))
    with pytest.raises(bot.ApplicationHandlerStop):
        run(bot.enforce_allowlist(seen.update("hi", user_id=999), ctx))
    run(bot.choose_action(seen.update("привет"), ctx))  # typed text without a number
    seen.texts += [c.description for c in bot.COMMANDS]
    seen.texts += [text_input.HINT, errors.SAVE_FAILED, errors.NOT_PARSED]
    assert_russian(seen.collect())


def test_manual_flow(seen: Seen, ctx: SimpleNamespace) -> None:
    m = bot.MANUAL
    run(m.begin(seen.update(nav.ADD_EXPENSE), ctx))
    run(m.choose_cat(seen.update("нет такой"), ctx))
    run(m.choose_cat(seen.update("Еда"), ctx))
    run(m.type_amount(seen.update("ноль"), ctx))
    run(m.type_amount(seen.update("1234.5"), ctx))
    run(m.choose_cur(seen.update("нет такой"), ctx))
    run(m.choose_cur(seen.update("долл"), ctx))
    run(m.on_currency_button(seen.update(query=FakeQuery("cur:page:0")), ctx))
    run(m.choose_cur(seen.update("дин"), ctx))
    run(m.type_comment(seen.update(query=FakeQuery("skip")), ctx))
    run(m.choose_dt(seen.update(query=FakeQuery("custom")), ctx))
    run(m.type_dt(seen.update("никогда"), ctx))
    run(m.type_dt(seen.update("13.07.2025"), ctx))
    run(m.begin(seen.update(nav.ADD_EXPENSE), ctx))
    run(m.choose_cat(seen.update("Еда"), ctx))
    run(m.type_amount(seen.update("350 дин"), ctx))
    run(m.today_no_comment(seen.update(query=FakeQuery(manual.TODAY_NO_COMMENT)), ctx))
    texts = seen.collect()
    assert "✅ Сохранено: Еда — 1 234,50 дин, 13.07.2025" in texts
    assert_russian(texts)


def test_large_expense_texts(seen: Seen, ctx: SimpleNamespace,
                             monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot.fx, "rate", lambda src, dst: 1.0)
    m = bot.MANUAL
    run(m.choose_cat(seen.update("Еда"), ctx))
    run(m.type_amount(seen.update("1500 €"), ctx))
    run(m.on_large_text(seen.update("может быть"), ctx))
    run(m.on_large_button(seen.update(query=FakeQuery(manual.LARGE_YES)), ctx))
    run(m.today_no_comment(seen.update(query=FakeQuery(manual.TODAY_NO_COMMENT)), ctx))
    large = Row(date=date(2026, 10, 7), month="2026-10", category="Транспорт",
                amount=2000.0, currency="€", who="Анна", comment="билеты", large=True)
    seen.texts.append(analytics.format_month_summary(
        analytics.month_summary([*ROWS, large], "2026-10")))
    texts = seen.collect()
    assert manual.LARGE_QUESTION in texts
    assert any("🔶 Крупные (не в итоге)\nбилеты — 2 000 €" in t for t in texts), texts
    assert_russian(texts)


def test_stats_flow(seen: Seen, ctx: SimpleNamespace) -> None:
    s = flow_stats.StatsFlow(
        categories=lambda _ctx: list(CATEGORIES),
        load_rows=lambda _ctx: list(ROWS),
        sheets=lambda _ctx: None,
        who=lambda _upd, _ctx: "Аня",
        all_codes=lambda: ["RSD", "EUR"],
        get_rate=lambda src, dst: 1.0 if src == dst else 117.0,
    )
    run(s.begin(seen.update(nav.SHOW_STATS), ctx))
    run(s.on_category(seen.update(flow_stats.ONE_BUTTON), ctx))
    run(s.on_category(seen.update("Еда"), ctx))
    run(s.on_type(seen.update("что-то"), ctx))
    run(s.on_type(seen.update(flow_stats.LAST_BUTTON), ctx))
    run(s.on_category(seen.update(flow_stats.ALL_BUTTON), ctx))
    run(s.on_type(seen.update(flow_stats.MONTHS_BUTTON), ctx))
    run(s.on_month(seen.update("2026-10"), ctx))
    run(s.on_group_currency(seen.update(flow_stats.YES_BUTTON), ctx))
    run(s.on_category(seen.update(flow_stats.ALL_BUTTON), ctx))
    run(s.on_type(seen.update(flow_stats.PERIOD_BUTTON), ctx))
    run(s.on_date_from(seen.update("никогда"), ctx))
    run(s.on_date_from(seen.update("01.10.2026"), ctx))
    run(s.on_date_to(seen.update("31.10.2026"), ctx))
    run(s.on_group_currency(seen.update(flow_stats.NO_BUTTON), ctx))
    run(s.on_convert_currency(seen.update("дин"), ctx))
    run(s.on_show_details(seen.update(flow_stats.DETAILS_BUTTON), ctx))
    run(s.on_category(seen.update(flow_stats.ONLY_LARGE_BUTTON), ctx))
    run(s.on_type(seen.update(flow_stats.LAST_BUTTON), ctx))
    run(s.on_category(seen.update(flow_stats.ONLY_LARGE_BUTTON), ctx))
    run(s.on_month(seen.update("2026-10"), ctx))
    run(s.on_group_currency(seen.update(flow_stats.YES_BUTTON), ctx))
    run(s.this_month(seen.update(nav.THIS_MONTH), ctx))
    seen.texts.append(analytics.format_last_n([], category="Еда"))
    texts = seen.collect()
    assert any(t.endswith("💰 Итого: 2 404,50 дин") for t in texts), texts
    assert_russian(texts)


def test_preset_flow(seen: Seen, ctx: SimpleNamespace) -> None:
    run(flow_preset.begin_preset_picker(seen.update(nav.ADD_PRESET), ctx, []))
    run(bot.choose_action(seen.update(nav.ADD_PRESET), ctx))
    run(bot.choose_preset(seen.update("что-то"), ctx))
    run(bot.choose_preset(seen.update("⚡ Нет такой"), ctx))
    run(bot.choose_preset(seen.update("⚡ Аренда"), ctx))
    run(bot.choose_preset(seen.update("⚡ Коммуналка"), ctx))
    run(bot.type_preset_amount(seen.update("ноль"), ctx))
    run(bot.type_preset_amount(seen.update("4 200"), ctx))
    texts = seen.collect()
    assert "✅ Сохранено: Аренда — 500 € · 19.11.2026" in texts
    assert "✅ Сохранено: Коммуналка — 4 200 дин · 19.11.2026" in texts
    assert_russian(texts)


def test_draft_card() -> None:
    session = draft.DraftSession(id="ab12", transcript="кофе", items=[
        {"comment": "кофе", "amount": 1234.0, "currency": "дин", "category": "Еда",
         "date": "19.11.2026", "who": "Анна", "large": True}])
    texts = [draft.card_text(session)]
    grids = [draft.main_buttons(session), draft.item_edit_buttons(session, 0),
             draft.category_buttons(session, 0, CATEGORIES),
             draft.currency_buttons(session, 0, ["дин"]), draft.date_buttons(session, 0)]
    codes = ["CNY", "EUR", "GEL", "RSD", "USD", "CAD"]
    views = [draft.apply_callback("cur:0:page:0", session, CATEGORIES, ["дин"],
                                  FakeSheetsClient(), all_codes=lambda: codes),
             draft.search_currency(session, 0, "тугрик", codes),
             draft.search_currency(session, 0, "долл", codes)]
    texts += [v.text or "" for v in views]
    grids += [v.buttons or [] for v in views]
    texts += [label for grid in grids for row in grid for label, _ in row]
    outcome = draft.apply_callback("save", session, CATEGORIES, ["дин"], FakeSheetsClient())
    texts.append(outcome.text or "")
    assert "• Сумма: 1 234 дин" in texts[0]
    assert outcome.text == "✅ Сохранено: кофе — 1 234 дин · 19.11.2026"
    assert_russian(texts)


def test_check_catches_english() -> None:
    assert english_words("📊 Show statistics") == ["Show", "statistics"]
    assert english_words("Добавьте ID в ALLOWED_USERS в .env, потом /reloadcats") == []


def test_payment_reminder_texts() -> None:
    from family_bot import payment_reminder as pr

    rent = {**PRESETS[0], "section": "monthly"}
    bills = {**PRESETS[1], "section": "monthly"}
    rows = [Row(date=date(2026, 11, 3), month="2026-11", category="Еда", amount=300.0,
                currency="дин", who="Анна", comment="кофе")]
    rem = pr.new_reminder(pr.due_payments([rent, bills], rows, "2026-11"), "2026-11")
    texts = [pr.message_text(rem), pr.STALE_TEXT, pr.CLOSED_TEXT, pr.NOT_YOURS_TEXT,
             pr.PROMPT_TEXT.format(name="Коммуналка", currency="дин")]
    texts += [label for row in pr.buttons(rem) for label, _ in row]
    rem.open.clear()
    texts.append(pr.message_text(rem))
    assert "уже есть: 03.11.2026 — 300 дин — кофе" in texts[0]
    assert_russian(texts)
