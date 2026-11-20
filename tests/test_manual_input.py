"""Manual add-expense steps: amount, Category and Currency checks (R6, R13, U3, R12)."""
import asyncio
from datetime import datetime, timezone

import pytest
from telegram import Chat, Message, Update, User
from telegram.ext import ApplicationHandlerStop

from family_bot import bot
from family_bot.conversation_states import State
from tests.conftest import bot_context
from tests.known_bugs.fakes import make_update

CHOOSE_CAT, CHOOSE_CUR = State.CHOOSE_CAT, State.CHOOSE_CUR
TYPING_AMT, TYPING_CMNT = State.TYPING_AMT, State.TYPING_CMNT


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def ctx():
    return bot_context(categories=["Food", "Transport"], currencies=["₽", "дин", "€", "$"],
                       allowed_users={111: "Azat"})


def _inline_labels(markup):
    return {btn.text: btn.callback_data for row in markup.inline_keyboard for btn in row}


def replies(update):
    return [t for t, _ in update.message.replies]


# --- Category ---

def test_known_category_is_accepted(ctx):
    assert run(bot.MANUAL.choose_cat(make_update("Food"), ctx)) == TYPING_AMT
    assert ctx.user_data["cat"] == "Food"


def test_category_match_ignores_case_and_spaces(ctx):
    assert run(bot.MANUAL.choose_cat(make_update("  food "), ctx)) == TYPING_AMT
    assert ctx.user_data["cat"] == "Food"


def test_category_typo_asks_to_pick_from_list(ctx):
    upd = make_update("Fod")
    assert run(bot.MANUAL.choose_cat(upd, ctx)) == CHOOSE_CAT
    assert "cat" not in ctx.user_data
    text, kw = upd.message.replies[-1]
    labels = [btn.text for row in kw["reply_markup"].keyboard for btn in row]
    assert "Food" in labels and "Transport" in labels, labels


# --- Amount ---

@pytest.mark.parametrize("text", ["nan", "inf", "-500", "0", "abc", "350 xyz"])
def test_bad_amount_stays_on_amount_step(ctx, text):
    upd = make_update(text)
    assert run(bot.MANUAL.type_amount(upd, ctx)) == TYPING_AMT
    assert "amt" not in ctx.user_data
    assert replies(upd), "user gets no hint"


def test_amount_with_thousands_space_goes_to_currency(ctx):
    assert run(bot.MANUAL.type_amount(make_update("1 200"), ctx)) == CHOOSE_CUR
    assert ctx.user_data["amt"] == 1200.0
    assert "cur" not in ctx.user_data


def test_amount_with_currency_skips_currency_step(ctx):
    upd = make_update("350 дин")
    assert run(bot.MANUAL.type_amount(upd, ctx)) == TYPING_CMNT
    assert ctx.user_data["amt"] == 350.0
    assert ctx.user_data["cur"] == "дин"
    assert ctx.user_data["spender"] == "Azat"


def test_amount_with_currency_off_the_buttons_asks_for_currency(ctx):
    # "¥" is registered but not on this install's buttons
    assert run(bot.MANUAL.type_amount(make_update("500 ¥"), ctx)) == CHOOSE_CUR
    assert ctx.user_data["amt"] == 500.0
    assert "cur" not in ctx.user_data


# --- Currency ---

@pytest.mark.parametrize("text,stored", [("€", "€"), ("eur", "€"), ("динар", "дин")])
def test_currency_by_symbol_code_or_word(ctx, text, stored):
    ctx.user_data["amt"] = 5.0
    assert run(bot.MANUAL.choose_cur(make_update(text), ctx)) == TYPING_CMNT
    assert ctx.user_data["cur"] == stored


@pytest.mark.parametrize("text", ["eru", "xyz", "кофе"])
def test_currency_typo_offers_recent_currencies(ctx, text):
    upd = make_update(text)
    assert run(bot.MANUAL.choose_cur(upd, ctx)) == CHOOSE_CUR
    assert "cur" not in ctx.user_data
    reply, kw = upd.message.replies[-1]
    assert reply.startswith("❌")
    labels = _inline_labels(kw["reply_markup"])
    assert {"🇷🇸 дин", "🇪🇺 €", "🇷🇺 ₽", "🌍 Другая"} <= set(labels), labels


# --- Currency picker (issue #33) ---

@pytest.mark.parametrize("text,stored", [("юань", "CNY"), ("GEL", "GEL"), ("лари", "GEL"),
                                         ("¥", "¥")])
def test_currency_found_by_name_or_code(ctx, text, stored):
    ctx.user_data["amt"] = 5.0
    assert run(bot.MANUAL.choose_cur(make_update(text), ctx)) == TYPING_CMNT
    assert ctx.user_data["cur"] == stored


def test_several_matches_are_offered_as_buttons(ctx):
    upd = make_update("долл")
    assert run(bot.MANUAL.choose_cur(upd, ctx)) == CHOOSE_CUR
    labels = _inline_labels(upd.message.replies[-1][1]["reply_markup"])
    assert {"🇨🇦 CAD", "🇺🇸 $"} <= set(labels), labels


def test_currency_step_shows_recent_currencies_with_flags(ctx):
    from tests.conftest import FakeSheetsClient, settings_of

    sheets = FakeSheetsClient()
    sheets.users[111] = ["CNY", "дин", "€"]
    settings_of(ctx).sheets = sheets
    upd = make_update("1 200")
    assert run(bot.MANUAL.type_amount(upd, ctx)) == CHOOSE_CUR
    markup = upd.message.replies[-1][1]["reply_markup"]
    rows = [[b.text for b in row] for row in markup.inline_keyboard]
    assert rows[0] == ["🇨🇳 CNY", "🇷🇸 дин", "🇪🇺 €"]
    assert rows[1] == ["🌍 Другая"]


def _tap(ctx, data):
    from tests.known_bugs.fakes import FakeQuery

    query = FakeQuery(data)
    state = run(bot.MANUAL.on_currency_button(make_update(query=query), ctx))
    return state, query


def test_other_pages_then_pick_saves_iso_code_and_recent(ctx):
    from tests.conftest import FakeSheetsClient, settings_of

    sheets = FakeSheetsClient()
    settings_of(ctx).sheets = sheets
    ctx.user_data["amt"] = 10.0
    state, query = _tap(ctx, "cur:page:0")
    assert state == CHOOSE_CUR
    assert query.edits[-1].startswith("🌍 Все валюты, стр. 1 из 1")
    labels = _inline_labels(query.markups[-1])
    assert labels["🇬🇪 GEL"] == "cur:pick:GEL"
    state, query = _tap(ctx, labels["🇬🇪 GEL"])
    assert state == TYPING_CMNT
    assert ctx.user_data["cur"] == "GEL"
    assert query.edits == ["💱 Валюта: 🇬🇪 GEL"]
    assert sheets.users == {}, "Recent currencies change only when the Expense is saved"


def test_picker_buttons_have_a_handler_in_currency_state():
    from telegram.ext import CallbackQueryHandler

    patterns = [h.pattern for h in bot.MANUAL.states()[CHOOSE_CUR]
                if isinstance(h, CallbackQueryHandler)]
    assert any(p.match("cur:pick:RSD") for p in patterns)
    assert any(p.match("cur:page:2") for p in patterns)


def test_saved_currency_goes_to_recent(ctx, monkeypatch):
    from family_bot.flows import manual
    from tests.conftest import FakeSheetsClient, settings_of

    sheets = FakeSheetsClient()
    sheets.users[111] = ["дин", "€", "₽"]
    settings_of(ctx).sheets = sheets
    monkeypatch.setattr(manual.clock, "now", lambda: datetime(2026, 11, 19))
    ctx.user_data["cat"] = "Food"
    assert run(bot.MANUAL.type_amount(make_update("350 $"), ctx)) == TYPING_CMNT
    assert sheets.users[111] == ["дин", "€", "₽"]
    run(bot.MANUAL.type_dt(make_update("19.11.2026"), ctx))
    assert sheets.users[111] == ["$", "дин", "€"]
    assert sheets.user_names[111] == "Azat"


# --- Comment and date ---

def test_skip_comment_then_today_saves_and_goes_home(ctx, monkeypatch):
    from family_bot.flows import manual
    from tests.conftest import FakeSheetsClient, settings_of
    from tests.known_bugs.fakes import FakeQuery

    sheets = FakeSheetsClient()
    settings_of(ctx).sheets = sheets
    monkeypatch.setattr(manual.clock, "now", lambda: datetime(2026, 11, 19))
    ctx.user_data.update(cat="Food", amt=350.0, cur="дин", spender="Azat")
    assert run(bot.MANUAL.type_comment(make_update(query=FakeQuery("skip")), ctx)) \
        == State.CHOOSE_DT
    query = FakeQuery("today")
    assert run(bot.MANUAL.choose_dt(make_update(query=query), ctx)) == State.CHOOSE_ACTION
    assert sheets.appended_rows[0][:7] == ["19.11.2026", "2026-11", "Food", 350.0, "дин",
                                           "Azat", ""]
    assert query.edits == ["✅ Сохранено: Food — 350 дин, 19.11.2026"]
    assert "cat" not in ctx.user_data


def test_today_no_comment_button_saves_today_with_empty_comment(ctx, monkeypatch):
    from family_bot.flows import manual
    from tests.conftest import FakeSheetsClient, settings_of
    from tests.known_bugs.fakes import FakeQuery

    sheets = FakeSheetsClient()
    settings_of(ctx).sheets = sheets
    monkeypatch.setattr(manual.clock, "now", lambda: datetime(2026, 11, 19))
    ctx.user_data.update(cat="Food", amt=350.0, cur="дин", spender="Azat", comment="old")
    query = FakeQuery(manual.TODAY_NO_COMMENT)
    assert run(bot.MANUAL.today_no_comment(make_update(query=query), ctx)) \
        == State.CHOOSE_ACTION
    assert sheets.appended_rows[0][:7] == ["19.11.2026", "2026-11", "Food", 350.0, "дин",
                                           "Azat", ""]
    assert query.edits == ["✅ Сохранено: Food — 350 дин, 19.11.2026"]
    assert "cat" not in ctx.user_data


def _tap_path_to_comment_step(ctx, taps):
    """Menu → Category → «350 дин»: returns the comment-step markup."""
    from family_bot import navigation as nav

    taps.append(nav.ADD_EXPENSE)
    run(bot.MANUAL.begin(make_update(nav.ADD_EXPENSE), ctx))
    taps.append("Food")
    run(bot.MANUAL.choose_cat(make_update("Food"), ctx))
    upd = make_update("350 дин")  # typed, not a tap
    assert run(bot.MANUAL.type_amount(upd, ctx)) == TYPING_CMNT
    return upd.message.replies[-1][1]["reply_markup"]


def test_coffee_350_din_today_tap_count(ctx, monkeypatch):
    """«кофе 350 дин сегодня» by hand: count taps on the old and the new path (U2)."""
    from telegram.ext import CallbackQueryHandler

    from family_bot.flows import manual
    from tests.conftest import FakeSheetsClient, settings_of
    from tests.known_bugs.fakes import FakeQuery

    settings_of(ctx).sheets = FakeSheetsClient()
    monkeypatch.setattr(manual.clock, "now", lambda: datetime(2026, 11, 19))

    old: list[str] = []
    labels = _inline_labels(_tap_path_to_comment_step(ctx, old))
    old.append("⏭️ Пропустить")
    query = FakeQuery(labels[old[-1]])
    run(bot.MANUAL.type_comment(make_update(query=query), ctx))
    labels = _inline_labels(query.message.replies[-1][1]["reply_markup"])
    old.append("📅 Сегодня")
    assert run(bot.MANUAL.choose_dt(make_update(query=FakeQuery(labels[old[-1]])), ctx)) \
        == State.CHOOSE_ACTION

    new: list[str] = []
    labels = _inline_labels(_tap_path_to_comment_step(ctx, new))
    new.append("✅ Сегодня, без комментария")
    data = labels[new[-1]]
    patterns = [h.pattern for h in bot.MANUAL.states()[TYPING_CMNT]
                if isinstance(h, CallbackQueryHandler)]
    assert any(p.match(data) for p in patterns), "button has no handler in TYPING_CMNT"
    assert run(bot.MANUAL.today_no_comment(make_update(query=FakeQuery(data)), ctx)) \
        == State.CHOOSE_ACTION

    # U2 counted 5 taps: the Currency tap is gone since «350 дин» (#15),
    # this button turns «⏭️ Пропустить» + «📅 Сегодня» into one tap.
    assert (len(old), len(new)) == (4, 3)
    assert len(settings_of(ctx).sheets.appended_rows) == 2


def test_typed_date_saves(ctx):
    from tests.conftest import FakeSheetsClient, settings_of

    settings_of(ctx).sheets = FakeSheetsClient()
    ctx.user_data.update(cat="Food", amt=5.0, cur="€", spender="Azat", comment="x")
    upd = make_update("13.07.2025")
    assert run(bot.MANUAL.type_dt(upd, ctx)) == State.CHOOSE_ACTION
    assert replies(upd)[0] == "✅ Сохранено: Food — 5 €, 13.07.2025"


# --- Edited messages (R12) ---

def test_main_registers_edit_drop_before_allowlist(monkeypatch):
    apps = []
    monkeypatch.setenv("BOT_TOKEN", "123:abc")
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    monkeypatch.setattr(bot.config, "load_env_file", lambda: None)
    monkeypatch.setattr(bot.Application, "run_polling", lambda self, **kw: apps.append(self))
    bot.main()
    groups = apps[0].handlers
    assert bot.EDITED_MESSAGES in groups[-3], groups
    assert min(groups) == -3, "something runs before the edit drop"

def _message(text):
    return Message(message_id=5, date=datetime.now(timezone.utc),
                   chat=Chat(id=111, type="private"),
                   from_user=User(id=111, first_name="A", is_bot=False), text=text)


def test_new_message_is_not_dropped():
    assert not bot.EDITED_MESSAGES.check_update(Update(update_id=1, message=_message("5000")))


def test_edited_message_is_dropped_before_other_handlers(ctx):
    upd = Update(update_id=1, edited_message=_message("5000"))
    assert bot.EDITED_MESSAGES.check_update(upd)
    with pytest.raises(ApplicationHandlerStop):
        run(bot.EDITED_MESSAGES.callback(upd, ctx))
