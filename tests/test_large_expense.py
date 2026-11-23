"""Large expense («Крупная трата»), issue #83: setting, decision, Data column,
manual-flow step, ⚡ Preset, «📅 Этот месяц» and undo."""
from __future__ import annotations

import asyncio
from datetime import date, datetime
from typing import Any, Optional

import pytest

from family_bot import analytics
from family_bot import bot
from family_bot import config
from family_bot import large_expense
from family_bot import undo
from family_bot.conversation_states import State
from family_bot.expense import ExpenseRecord, append_expense, append_expenses
from family_bot.sheet_data import Row, parse_rows
from tests.conftest import FakeSheetsClient, bot_context, settings_of
from tests.known_bugs.fakes import FakeQuery, make_update

HEADERS = ["Date", "Month", "Category", "Amount", "Currency", "Who", "Comment"]
RSD_PER_EUR = 117.0


def run(coro: Any) -> Any:
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def eur_rate(src: str, dst: str) -> Optional[float]:
    """Stub rates: € is the base, дин trades at RSD_PER_EUR, others have no rate."""
    table = {("дин", "€"): 1 / RSD_PER_EUR, ("€", "€"): 1.0}
    return table.get((src, dst))


# --- Setting ---

def test_threshold_default_is_1000() -> None:
    assert config.load({}).large_threshold == 1000


def test_threshold_from_setting() -> None:
    assert config.load({"LARGE_EXPENSE_THRESHOLD": "500"}).large_threshold == 500


@pytest.mark.parametrize("raw", ["много", "-5", "0", "nan", "inf"])
def test_bad_threshold_is_one_line_and_keeps_default(raw: str) -> None:
    cfg = config.load({"LARGE_EXPENSE_THRESHOLD": raw})
    assert cfg.large_threshold == 1000
    lines = [p for p in cfg.problems if "LARGE_EXPENSE_THRESHOLD" in p]
    assert len(lines) == 1 and "\n" not in lines[0]


# --- Decision ---

@pytest.mark.parametrize("amount,cur,expected", [
    (1500, "€", True),
    (1000, "€", False),  # equal to the threshold is not above it
    (300, "€", False),
    (150_000, "дин", True),
    (100_000, "дин", False),
    (5000, "CNY", False),  # no rate: never suggested
])
def test_suggest(amount: float, cur: str, expected: bool) -> None:
    assert large_expense.suggest(amount, cur, threshold=1000, threshold_currency="€",
                                 get_rate=eur_rate) is expected


def test_suggest_compares_eur_and_its_iso_code_without_rate() -> None:
    assert large_expense.suggest(2000, "EUR", threshold=1000, threshold_currency="€",
                                 get_rate=lambda s, d: None)


# --- Data column ---

def test_record_without_mark_keeps_old_layout() -> None:
    record = ExpenseRecord(date="07.08.2026", category="Food", amount=5, currency="€",
                           who="Ann")
    assert record.to_row(HEADERS) == ["07.08.2026", "2026-08", "Food", 5, "€", "Ann", ""]


def test_marked_record_writes_da_in_large_column() -> None:
    record = ExpenseRecord(date="07.08.2026", category="Trips", amount=2000, currency="€",
                           who="Ann", comment="tickets", large=True)
    assert record.to_row([*HEADERS, "Large"])[-1] == "да"
    assert record.to_row([*HEADERS, "Крупная"])[-1] == "да"


def test_first_marked_write_adds_the_column() -> None:
    sheets = FakeSheetsClient(data_values=[HEADERS])
    append_expense(sheets, ExpenseRecord(date="07.08.2026", category="Food", amount=5,
                                         currency="€", who="Ann"))
    assert sheets.read_data_headers() == HEADERS  # unmarked: no new column
    append_expense(sheets, ExpenseRecord(date="08.08.2026", category="Trips", amount=2000,
                                         currency="€", who="Ann", large=True))
    assert sheets.read_data_headers() == [*HEADERS, "Large"]
    rows = parse_rows(sheets.read_data_values()[0], sheets.read_data_values()[1:])
    assert [r.large for r in rows] == [False, True]


def test_batch_with_a_marked_record_adds_the_column_once() -> None:
    sheets = FakeSheetsClient(data_values=[[*HEADERS[:-1], "Комментарий"]])
    records = [ExpenseRecord(date="08.08.2026", category="Trips", amount=2000, currency="€",
                             who="Ann", large=True)] * 2
    append_expenses(sheets, records)
    assert sheets.read_data_headers()[-1] == "Large"
    assert sheets.read_data_headers().count("Large") == 1


def test_russian_large_header_is_used_not_duplicated() -> None:
    sheets = FakeSheetsClient(data_values=[[*HEADERS, "Крупная"]])
    append_expense(sheets, ExpenseRecord(date="08.08.2026", category="Trips", amount=2000,
                                         currency="€", who="Ann", large=True))
    assert sheets.read_data_headers() == [*HEADERS, "Крупная"]
    assert sheets.appended_rows[-1][-1] == "да"


def test_rows_without_column_are_not_large() -> None:
    rows = parse_rows(HEADERS, [["07.08.2026", "", "Food", "5", "€", "Ann", ""]])
    assert rows[0].large is False


@pytest.mark.parametrize("cell,expected", [
    ("да", True), ("Да", True), ("x", True), ("1", True), ("TRUE", True),
    ("", False), ("нет", False), ("Нет", False), ("no", False), ("0", False),
    ("FALSE", False), ("  ", False),
])
def test_hand_mark_in_the_sheet_is_read(cell: str, expected: bool) -> None:
    rows = parse_rows([*HEADERS, "Крупная"],
                      [["07.08.2026", "", "Food", "5", "€", "Ann", "", cell]])
    assert rows[0].large is expected


def test_bootstrap_layout_has_large_column() -> None:
    from family_bot.sheets import DATA_HEADERS

    assert DATA_HEADERS[-1] == "Large"


# --- Undo ---

def test_undo_deletes_a_large_expense_even_after_hand_unmark() -> None:
    sheets = FakeSheetsClient(data_values=[[*HEADERS, "Large"]])
    record = ExpenseRecord(date="08.08.2026", category="Trips", amount=2000, currency="€",
                           who="Ann", large=True)
    append_expense(sheets, record)
    sheets._data_values[-1][-1] = "нет"  # someone unmarked it in the sheet
    user_data: dict = {}
    now = datetime(2026, 8, 8, 12)
    [[(_, data)]] = undo.remember(user_data, [record], now)
    assert undo.undo(user_data, data, sheets, now).done
    assert sheets.read_data_values() == [[*HEADERS, "Large"]]


# --- Manual flow ---

@pytest.fixture
def ctx() -> Any:
    context = bot_context(categories=["Food", "Trips"], currencies=["дин", "€"],
                          allowed_users={111: "Ann"}, sheets=FakeSheetsClient(
                              data_values=[HEADERS]))
    context.user_data["cat"] = "Trips"
    return context


@pytest.fixture
def rates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot.fx, "rate", eur_rate)


def _labels(markup: Any) -> dict[str, str]:
    return {b.text: b.callback_data for row in markup.inline_keyboard for b in row}


def _save_today(ctx: Any, monkeypatch: pytest.MonkeyPatch) -> list:
    from family_bot.flows import manual

    monkeypatch.setattr(manual.clock, "now", lambda: datetime(2026, 11, 19))
    query = FakeQuery(manual.TODAY_NO_COMMENT)
    assert run(bot.MANUAL.today_no_comment(make_update(query=query), ctx)) \
        == State.CHOOSE_ACTION
    return settings_of(ctx).sheets.appended_rows


def test_above_threshold_asks_before_comment(ctx: Any, rates: None) -> None:
    upd = make_update("1500 €")
    assert run(bot.MANUAL.type_amount(upd, ctx)) == State.ASK_LARGE
    text, kw = upd.message.replies[-1]
    assert text == "🔶 Это крупная трата?"
    labels = _labels(kw["reply_markup"])
    assert {"Да", "Нет"} <= set(labels)


def test_other_currency_above_threshold_asks(ctx: Any, rates: None) -> None:
    assert run(bot.MANUAL.type_amount(make_update("150000 дин"), ctx)) == State.ASK_LARGE


def test_below_threshold_goes_straight_to_comment(ctx: Any, rates: None) -> None:
    assert run(bot.MANUAL.type_amount(make_update("350 €"), ctx)) == State.TYPING_CMNT


def test_no_rate_goes_straight_to_comment(ctx: Any) -> None:
    # conftest: the rate service is offline
    assert run(bot.MANUAL.type_amount(make_update("150000 дин"), ctx)) == State.TYPING_CMNT


def test_threshold_setting_is_used(ctx: Any, rates: None) -> None:
    settings_of(ctx).large_threshold = 400
    assert run(bot.MANUAL.type_amount(make_update("500 €"), ctx)) == State.ASK_LARGE


def test_yes_then_today_no_comment_saves_marked(ctx: Any, rates: None,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    upd = make_update("1500 €")
    run(bot.MANUAL.type_amount(upd, ctx))
    yes = _labels(upd.message.replies[-1][1]["reply_markup"])["Да"]
    query = FakeQuery(yes)
    assert run(bot.MANUAL.on_large_button(make_update(query=query), ctx)) == State.TYPING_CMNT
    comment_markup = query.message.replies[-1][1]["reply_markup"]
    assert "✅ Сегодня, без комментария" in _labels(comment_markup)
    rows = _save_today(ctx, monkeypatch)
    assert rows[-1] == ["19.11.2026", "2026-11", "Trips", 1500.0, "€", "Ann", "", "да"]
    assert settings_of(ctx).sheets.read_data_headers() == [*HEADERS, "Large"]


def test_no_saves_unmarked(ctx: Any, rates: None, monkeypatch: pytest.MonkeyPatch) -> None:
    upd = make_update("1500 €")
    run(bot.MANUAL.type_amount(upd, ctx))
    no = _labels(upd.message.replies[-1][1]["reply_markup"])["Нет"]
    run(bot.MANUAL.on_large_button(make_update(query=FakeQuery(no)), ctx))
    rows = _save_today(ctx, monkeypatch)
    assert rows[-1] == ["19.11.2026", "2026-11", "Trips", 1500.0, "€", "Ann", ""]
    assert settings_of(ctx).sheets.read_data_headers() == HEADERS


@pytest.mark.parametrize("text,state", [("да", State.TYPING_CMNT), ("Нет", State.TYPING_CMNT),
                                        ("может", State.ASK_LARGE)])
def test_typed_answer(ctx: Any, rates: None, text: str, state: State) -> None:
    run(bot.MANUAL.type_amount(make_update("1500 €"), ctx))
    assert run(bot.MANUAL.on_large_text(make_update(text), ctx)) == state


def test_large_step_has_handlers() -> None:
    from telegram.ext import CallbackQueryHandler

    patterns = [h.pattern for h in bot.MANUAL.states()[State.ASK_LARGE]
                if isinstance(h, CallbackQueryHandler)]
    from family_bot.flows import manual

    assert any(p.match(manual.LARGE_YES) for p in patterns)
    assert any(p.match(manual.LARGE_NO) for p in patterns)


def test_previous_answer_does_not_leak_into_next_expense(
        ctx: Any, rates: None, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx.user_data["large"] = True  # left over from an abandoned flow
    run(bot.MANUAL.type_amount(make_update("350 €"), ctx))
    assert _save_today(ctx, monkeypatch)[-1][-1] == ""


# --- ⚡ Preset ---

def test_preset_above_threshold_is_never_marked(ctx: Any, rates: None,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    settings_of(ctx).presets = [{"name": "Rent", "amount": 1500.0, "currency": "€",
                                 "category": "Trips", "who": "Ann"}]
    monkeypatch.setattr(bot.clock, "now", lambda: datetime(2026, 11, 19))
    assert run(bot.choose_preset(make_update("⚡ Rent"), ctx)) == State.CHOOSE_ACTION
    sheets = settings_of(ctx).sheets
    assert sheets.appended_rows[-1][:7] == ["19.11.2026", "2026-11", "Trips", 1500.0, "€",
                                            "Ann", "Rent"]
    assert sheets.read_data_headers() == HEADERS


# --- «📅 Этот месяц» and the summary on the 1st ---

def _row(amount: float, cur: str, comment: str = "", large: bool = False,
         category: str = "Food", month: str = "2026-11") -> Row:
    return Row(date=date(2026, 11, 3), month=month, category=category, amount=amount,
               currency=cur, who="Ann", comment=comment, large=large)


def test_month_summary_skips_large_and_lists_them_below() -> None:
    rows = [
        _row(300, "€"),
        _row(2000, "€", "tickets", large=True, category="Trips"),
        _row(150_000, "дин", "laptop", large=True, category="Tech"),
        _row(5000, "€", "old", large=True, month="2026-10"),
    ]
    text = analytics.format_month_summary(analytics.month_summary(rows, "2026-11"))
    assert "💰 Итого <b>300 €</b>" in text.splitlines()
    assert "<b>Trips</b>" not in text
    assert text.endswith("🔶 Крупные (не в итоге)\n"
                         "tickets — 2 000 €\nlaptop — 150 000 дин</blockquote>")


def test_month_summary_without_large_has_no_extra_line() -> None:
    text = analytics.format_month_summary(analytics.month_summary([_row(300, "€")], "2026-11"))
    assert "Крупные" not in text


def test_month_with_only_large_expenses_still_lists_them() -> None:
    text = analytics.format_month_summary(
        analytics.month_summary([_row(2000, "€", large=True, category="Trips")], "2026-11"))
    assert text.endswith("Трат нет\n\n<blockquote expandable>"
                         "🔶 Крупные (не в итоге)\nTrips — 2 000 €</blockquote>")


def test_preset_without_amount_above_threshold_is_never_marked(
        ctx: Any, rates: None, monkeypatch: pytest.MonkeyPatch) -> None:
    settings_of(ctx).presets = [{"name": "Utilities", "amount": None, "currency": "€",
                                 "category": "Trips", "who": "Ann"}]
    monkeypatch.setattr(bot.clock, "now", lambda: datetime(2026, 11, 19))
    assert run(bot.choose_preset(make_update("⚡ Utilities"), ctx)) \
        == State.TYPING_PRESET_AMT
    assert run(bot.type_preset_amount(make_update("1500"), ctx)) == State.CHOOSE_ACTION
    sheets = settings_of(ctx).sheets
    assert sheets.appended_rows[-1][:7] == ["19.11.2026", "2026-11", "Trips", 1500.0, "€",
                                            "Ann", "Utilities"]
    assert sheets.read_data_headers() == HEADERS


# --- Threshold Currency (bug: BASE_CURRENCY=RSD made the threshold 1000 дин) ---

def test_threshold_is_in_euro_whatever_the_base_currency() -> None:
    cfg = config.load({"BASE_CURRENCY": "RSD"})
    assert (cfg.large_threshold, cfg.large_threshold_currency) == (1000, "€")


@pytest.mark.parametrize("raw,expected", [
    ("1000 EUR", (1000, "€")),
    ("117 000 дин", (117_000, "дин")),
    ("RSD 50000", (50_000, "дин")),
    ("2000", (2000, "€")),
])
def test_threshold_setting_may_name_its_currency(raw: str, expected: tuple) -> None:
    cfg = config.load({"LARGE_EXPENSE_THRESHOLD": raw, "BASE_CURRENCY": "RSD"})
    assert (cfg.large_threshold, cfg.large_threshold_currency) == expected
    assert not [p for p in cfg.problems if "LARGE_EXPENSE_THRESHOLD" in p]


def test_manual_input_with_dinar_base_asks_only_above_1000_euro(
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot.fx, "rate", eur_rate)
    ctx = bot_context(categories=["Food"], currencies=["дин", "€"],
                      default_currency="дин", sheets=FakeSheetsClient())
    ctx.user_data["cat"] = "Food"
    assert run(bot.MANUAL.type_amount(make_update("1500 дин"), ctx)) == State.TYPING_CMNT
    ctx.user_data["cat"] = "Food"
    assert run(bot.MANUAL.type_amount(make_update("150000 дин"), ctx)) == State.ASK_LARGE
