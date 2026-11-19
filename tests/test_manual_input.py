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
    assert run(bot.MANUAL.choose_cur(make_update(text), ctx)) == TYPING_CMNT
    assert ctx.user_data["cur"] == stored


@pytest.mark.parametrize("text", ["eru", "¥", "xyz"])
def test_currency_typo_asks_to_pick_from_list(ctx, text):
    upd = make_update(text)
    assert run(bot.MANUAL.choose_cur(upd, ctx)) == CHOOSE_CUR
    assert "cur" not in ctx.user_data
    _, kw = upd.message.replies[-1]
    labels = [btn.text for row in kw["reply_markup"].keyboard for btn in row]
    assert "€" in labels and "дин" in labels, labels


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
    assert bot.EDITED_MESSAGES in groups[-2], groups
    assert min(groups) == -2, "something runs before the edit drop"

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
