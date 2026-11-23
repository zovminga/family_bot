"""Manual add-expense as one live card (issue #103; earlier R6, R13, U3, R12).

Spec docs/specs/2026-11-bot-look.md, stories 17–25. Tests check what the person
sees: which bot messages are in the chat, their text and buttons, which
answers are deleted.
"""
import asyncio
from datetime import datetime, timezone

import pytest
from telegram import Chat, Message, Update, User
from telegram.ext import ApplicationHandlerStop, CallbackQueryHandler

from family_bot import bot
from family_bot import live_card
from family_bot import navigation as nav
from family_bot.conversation_states import State
from family_bot.flows import manual
from tests.conftest import FakeSheetsClient, bot_context, settings_of
from tests.known_bugs.fakes import FakeBot, FakeMessage, FakeQuery, make_update

CHOOSE_CAT, CHOOSE_CUR = State.CHOOSE_CAT, State.CHOOSE_CUR
TYPING_AMT, TYPING_CMNT = State.TYPING_AMT, State.TYPING_CMNT


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def tg():
    return FakeBot()


@pytest.fixture
def ctx(tg, monkeypatch):
    monkeypatch.setattr(manual.clock, "now", lambda: datetime(2026, 11, 19))
    context = bot_context(categories=["Food", "Transport"], currencies=["₽", "дин", "€", "$"],
                          allowed_users={111: "Azat"}, sheets=FakeSheetsClient())
    context.bot = tg
    return context


def in_chat(tg):
    """The bot's messages still in the chat."""
    return [m for m in tg.sent if not m.deleted]


def view(message):
    """(text, inline labels -> callback data) as the message shows now."""
    text, kw = message.edits[-1] if message.edits else (message.text, message.kw)
    markup = kw.get("reply_markup")
    labels = {b.text: b.callback_data for row in markup.inline_keyboard for b in row} \
        if markup is not None else {}
    return text, labels


def card(ctx):
    return ctx.user_data[live_card.KEY_CARD]


def say(ctx, tg, text, handler):
    """The person types text on the current step; returns (state, their message)."""
    upd = make_update(text, bot=tg)
    return run(handler(upd, ctx)), upd.message


def tap(ctx, tg, label, handler):
    """The person taps a button on the card by its label."""
    data = view(card(ctx))[1][label]
    return run(handler(make_update(query=FakeQuery(data, message=card(ctx))), ctx))


def begin(ctx, tg):
    assert say(ctx, tg, nav.ADD_EXPENSE, bot.MANUAL.begin)[0] == TYPING_AMT


# --- The whole path ---

def test_coffee_350_din_from_menu_to_saved_leaves_one_bot_message(ctx, tg):
    begin(ctx, tg)
    state, amount = say(ctx, tg, "350 дин", bot.MANUAL.type_amount)
    assert state == CHOOSE_CAT
    assert tap(ctx, tg, "Food", bot.MANUAL.on_category_button) == TYPING_CMNT
    state, comment = say(ctx, tg, "кофе", bot.MANUAL.type_comment)
    assert state == State.CHOOSE_DT
    assert tap(ctx, tg, "📅 Сегодня", bot.MANUAL.choose_dt) == State.CHOOSE_ACTION

    assert amount.deleted and comment.deleted
    [saved] = in_chat(tg)
    text, labels = view(saved)
    assert text == ("✅ Сохранено\n<blockquote><b>кофе — 350 дин</b>\n"
                    "Food · 19.11 · Azat</blockquote>")
    assert list(labels) == ["↩️ Отменить"]
    assert settings_of(ctx).sheets.appended_rows[0][:7] == [
        "19.11.2026", "2026-11", "Food", 350.0, "дин", "Azat", "кофе"]
    assert live_card.KEY_CARD not in ctx.user_data and "amt" not in ctx.user_data


def test_card_shows_filled_fields_on_top_and_step_buttons_below(ctx, tg):
    begin(ctx, tg)
    say(ctx, tg, "350 дин", bot.MANUAL.type_amount)
    text, labels = view(card(ctx))
    assert text.splitlines()[:3] == ["💰 <b>Новая трата</b>", "Сумма: <b>350 дин</b>",
                                     "Кто: <b>Azat</b>"]
    assert text.endswith(manual.ASK_CATEGORY)
    assert list(labels) == ["Food", "Transport", "🏠 В начало"]
    tap(ctx, tg, "Transport", bot.MANUAL.on_category_button)
    text, labels = view(card(ctx))
    assert "Категория: <b>Transport</b>" in text
    assert list(labels) == ["✅ Сегодня, без комментария", "⏭️ Пропустить", "🏠 В начало"]


# --- Amount ---

@pytest.mark.parametrize("text", ["nan", "inf", "-500", "0", "abc", "350 xyz"])
def test_bad_amount_is_deleted_and_card_shows_reason_on_same_step(ctx, tg, text):
    begin(ctx, tg)
    state, reply = say(ctx, tg, text, bot.MANUAL.type_amount)
    assert state == TYPING_AMT
    assert reply.deleted
    assert "amt" not in ctx.user_data
    [shown] = in_chat(tg)
    shown_text, _ = view(shown)
    assert manual.AMOUNT_ERROR in shown_text and shown_text.endswith(manual.ASK_AMOUNT)
    # the same step takes the next good answer
    assert say(ctx, tg, "350", bot.MANUAL.type_amount)[0] == CHOOSE_CUR


def test_amount_with_thousands_space_goes_to_currency(ctx, tg):
    assert say(ctx, tg, "1 200", bot.MANUAL.type_amount)[0] == CHOOSE_CUR
    assert ctx.user_data["amt"] == 1200.0
    assert "cur" not in ctx.user_data


def test_amount_with_currency_skips_currency_step(ctx, tg):
    assert say(ctx, tg, "350 дин", bot.MANUAL.type_amount)[0] == CHOOSE_CAT
    assert (ctx.user_data["amt"], ctx.user_data["cur"]) == (350.0, "дин")
    assert ctx.user_data["spender"] == "Azat"


def test_amount_with_currency_off_the_buttons_asks_for_currency(ctx, tg):
    # "¥" is registered but not on this install's buttons
    assert say(ctx, tg, "500 ¥", bot.MANUAL.type_amount)[0] == CHOOSE_CUR
    assert "cur" not in ctx.user_data


# --- Currency ---

@pytest.mark.parametrize("text,stored", [("€", "€"), ("eur", "€"), ("динар", "дин"),
                                         ("юань", "CNY"), ("GEL", "GEL"), ("лари", "GEL"),
                                         ("¥", "¥")])
def test_typed_currency_is_taken_and_deleted(ctx, tg, text, stored):
    begin(ctx, tg)
    say(ctx, tg, "5", bot.MANUAL.type_amount)
    state, reply = say(ctx, tg, text, bot.MANUAL.choose_cur)
    assert state == CHOOSE_CAT
    assert ctx.user_data["cur"] == stored
    assert reply.deleted and len(in_chat(tg)) == 1


@pytest.mark.parametrize("text", ["eru", "xyz", "кофе"])
def test_currency_typo_shows_reason_and_recent_currencies(ctx, tg, text):
    begin(ctx, tg)
    say(ctx, tg, "5", bot.MANUAL.type_amount)
    state, reply = say(ctx, tg, text, bot.MANUAL.choose_cur)
    assert state == CHOOSE_CUR and "cur" not in ctx.user_data
    assert reply.deleted and len(in_chat(tg)) == 1
    shown, labels = view(card(ctx))
    assert "❌ Не нашёл такую валюту." in shown
    assert {"🇷🇸 дин", "🇪🇺 €", "🇷🇺 ₽", "🌍 Другая"} <= set(labels), labels


def test_several_matches_are_offered_as_buttons_on_the_card(ctx, tg):
    begin(ctx, tg)
    say(ctx, tg, "5", bot.MANUAL.type_amount)
    assert say(ctx, tg, "долл", bot.MANUAL.choose_cur)[0] == CHOOSE_CUR
    assert {"🇨🇦 CAD", "🇺🇸 $"} <= set(view(card(ctx))[1])
    assert len(in_chat(tg)) == 1


def test_currency_step_shows_recent_currencies_with_flags(ctx, tg):
    settings_of(ctx).sheets.users[111] = ["CNY", "дин", "€"]
    begin(ctx, tg)
    say(ctx, tg, "1 200", bot.MANUAL.type_amount)
    markup = card(ctx).edits[-1][1]["reply_markup"]
    rows = [[b.text for b in row] for row in markup.inline_keyboard]
    assert rows == [["🇨🇳 CNY", "🇷🇸 дин", "🇪🇺 €"], ["🌍 Другая"], ["🏠 В начало"]]


def test_other_pages_then_pick_on_the_same_card(ctx, tg):
    sheets = settings_of(ctx).sheets
    begin(ctx, tg)
    say(ctx, tg, "10", bot.MANUAL.type_amount)
    assert tap(ctx, tg, "🌍 Другая", bot.MANUAL.on_currency_button) == CHOOSE_CUR
    shown, labels = view(card(ctx))
    assert "🌍 Все валюты, стр. 1 из 1" in shown
    assert labels["🇬🇪 GEL"] == "cur:pick:GEL"
    assert tap(ctx, tg, "🇬🇪 GEL", bot.MANUAL.on_currency_button) == CHOOSE_CAT
    assert ctx.user_data["cur"] == "GEL"
    assert "Сумма: <b>10 GEL</b>" in view(card(ctx))[0]
    assert len(in_chat(tg)) == 1
    assert sheets.users == {}, "Recent currencies change only when the Expense is saved"


def test_picker_buttons_have_a_handler_in_currency_state():
    patterns = [h.pattern for h in bot.MANUAL.states()[CHOOSE_CUR]
                if isinstance(h, CallbackQueryHandler)]
    assert any(p.match("cur:pick:RSD") for p in patterns)
    assert any(p.match("cur:page:2") for p in patterns)


def test_saved_currency_goes_to_recent(ctx, tg):
    sheets = settings_of(ctx).sheets
    sheets.users[111] = ["дин", "€", "₽"]
    begin(ctx, tg)
    say(ctx, tg, "350 $", bot.MANUAL.type_amount)
    tap(ctx, tg, "Food", bot.MANUAL.on_category_button)
    tap(ctx, tg, "✅ Сегодня, без комментария", bot.MANUAL.today_no_comment)
    assert sheets.users[111] == ["$", "дин", "€"]
    assert sheets.user_names[111] == "Azat"


# --- Category ---

def test_typed_category_ignores_case_and_spaces(ctx, tg):
    begin(ctx, tg)
    say(ctx, tg, "350 дин", bot.MANUAL.type_amount)
    state, reply = say(ctx, tg, "  food ", bot.MANUAL.choose_cat)
    assert state == TYPING_CMNT and ctx.user_data["cat"] == "Food"
    assert reply.deleted


def test_category_typo_shows_reason_and_the_list(ctx, tg):
    begin(ctx, tg)
    say(ctx, tg, "350 дин", bot.MANUAL.type_amount)
    state, reply = say(ctx, tg, "Fod", bot.MANUAL.choose_cat)
    assert state == CHOOSE_CAT and "cat" not in ctx.user_data
    assert reply.deleted
    shown, labels = view(card(ctx))
    assert shown.endswith(manual.UNKNOWN_CATEGORY)
    assert {"Food", "Transport"} <= set(labels)


def test_category_button_from_a_reloaded_list_asks_again(ctx, tg):
    begin(ctx, tg)
    say(ctx, tg, "350 дин", bot.MANUAL.type_amount)
    query = FakeQuery("mcat:7", message=card(ctx))
    assert run(bot.MANUAL.on_category_button(make_update(query=query), ctx)) == CHOOSE_CAT
    assert "cat" not in ctx.user_data


# --- Comment and date ---

def _to_comment_step(ctx, tg):
    begin(ctx, tg)
    say(ctx, tg, "350 дин", bot.MANUAL.type_amount)
    tap(ctx, tg, "Food", bot.MANUAL.on_category_button)


def test_skip_comment_then_today_saves(ctx, tg):
    _to_comment_step(ctx, tg)
    assert tap(ctx, tg, "⏭️ Пропустить", bot.MANUAL.type_comment) == State.CHOOSE_DT
    assert tap(ctx, tg, "📅 Сегодня", bot.MANUAL.choose_dt) == State.CHOOSE_ACTION
    assert settings_of(ctx).sheets.appended_rows[0][:7] == [
        "19.11.2026", "2026-11", "Food", 350.0, "дин", "Azat", ""]
    [saved] = in_chat(tg)
    assert view(saved)[0] == ("✅ Сохранено\n<blockquote><b>Food — 350 дин</b>\n"
                              "19.11 · Azat</blockquote>")


def test_today_no_comment_button_saves_today_with_empty_comment(ctx, tg):
    _to_comment_step(ctx, tg)
    assert view(card(ctx))[1]  # comment step buttons are on the card
    label = "✅ Сегодня, без комментария"
    data = view(card(ctx))[1][label]
    patterns = [h.pattern for h in bot.MANUAL.states()[TYPING_CMNT]
                if isinstance(h, CallbackQueryHandler)]
    assert any(p.match(data) for p in patterns), "button has no handler in TYPING_CMNT"
    assert tap(ctx, tg, label, bot.MANUAL.today_no_comment) == State.CHOOSE_ACTION
    assert settings_of(ctx).sheets.appended_rows[0][:7] == [
        "19.11.2026", "2026-11", "Food", 350.0, "дин", "Azat", ""]
    assert len(in_chat(tg)) == 1


def test_coffee_350_din_today_tap_count(ctx, tg):
    """«кофе 350 дин сегодня» by hand: two taps after the menu (U2 counted 5)."""
    taps = [nav.ADD_EXPENSE]
    begin(ctx, tg)
    say(ctx, tg, "350 дин", bot.MANUAL.type_amount)  # typed, not a tap
    taps.append("Food")
    tap(ctx, tg, "Food", bot.MANUAL.on_category_button)
    taps.append("✅ Сегодня, без комментария")
    assert tap(ctx, tg, taps[-1], bot.MANUAL.today_no_comment) == State.CHOOSE_ACTION
    assert len(taps) == 3


def test_typed_date_saves_and_bad_date_shows_reason(ctx, tg):
    _to_comment_step(ctx, tg)
    tap(ctx, tg, "⏭️ Пропустить", bot.MANUAL.type_comment)
    assert tap(ctx, tg, "📆 Другая дата", bot.MANUAL.choose_dt) == State.TYPING_DT
    state, bad = say(ctx, tg, "никогда", bot.MANUAL.type_dt)
    assert state == State.TYPING_DT and bad.deleted
    assert manual.BAD_DATE in view(card(ctx))[0]
    state, good = say(ctx, tg, "13.07.2025", bot.MANUAL.type_dt)
    assert state == State.CHOOSE_ACTION and good.deleted
    [saved] = in_chat(tg)
    assert "Food — 350 дин</b>\n13.07 · Azat" in view(saved)[0]


# --- Leaving mid-input, a buried card ---

def test_home_button_mid_input_removes_the_card(ctx, tg):
    begin(ctx, tg)
    say(ctx, tg, "350 дин", bot.MANUAL.type_amount)
    the_card = card(ctx)
    query = FakeQuery(nav.HOME_CALLBACK, message=the_card)
    assert run(nav.on_home(make_update(query=query), ctx)) == State.CHOOSE_ACTION
    assert the_card.deleted
    assert live_card.KEY_CARD not in ctx.user_data and "amt" not in ctx.user_data


def test_cancel_mid_input_removes_the_card(ctx, tg):
    begin(ctx, tg)
    the_card = card(ctx)
    assert run(bot.cancel(make_update("/cancel", bot=tg), ctx)) == State.CHOOSE_ACTION
    assert the_card.deleted


def test_menu_button_mid_input_removes_the_card_and_does_its_job(ctx, tg):
    begin(ctx, tg)
    old = card(ctx)
    upd = make_update(nav.ADD_EXPENSE, bot=tg)
    assert run(bot.menu_mid_input(upd, ctx)) == TYPING_AMT
    assert old.deleted
    assert in_chat(tg) == [card(ctx)] and card(ctx) is not old


def test_menu_buttons_are_caught_before_step_input():
    from telegram.ext import ConversationHandler

    app = bot.build_application("123:abc")
    conv = next(h for h in app.handlers[0] if isinstance(h, ConversationHandler))
    for state, handlers in conv.states.items():
        if state in bot.MANUAL.states():
            assert handlers[0] is bot.MENU_MID_INPUT, state


def test_new_input_removes_a_card_left_by_start(ctx, tg):
    begin(ctx, tg)
    old = card(ctx)
    run(bot.start(make_update("/start", bot=tg), ctx))
    begin(ctx, tg)
    assert old.deleted and not card(ctx).deleted


def test_buried_card_is_sent_again_at_the_bottom(ctx, tg):
    begin(ctx, tg)
    old = card(ctx)
    FakeMessage("🎧 Распознаю…", bot=tg, from_bot=True)  # e.g. a voice Draft below it
    state, _ = say(ctx, tg, "350 дин", bot.MANUAL.type_amount)
    assert state == CHOOSE_CAT
    assert old.deleted
    new = card(ctx)
    assert new is not old and in_chat(tg)[-1] is new
    assert "Сумма: <b>350 дин</b>" in view(new)[0]


def test_group_chat_keeps_answers(ctx, tg):
    begin(ctx, tg)
    upd = make_update("350 дин", bot=tg)
    upd.message.chat.type = "group"
    run(bot.MANUAL.type_amount(upd, ctx))
    assert not upd.message.deleted


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
