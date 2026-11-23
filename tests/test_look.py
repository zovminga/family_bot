"""HTML in every message and button styles (issue #100).

Every bot message is parsed as Telegram HTML, so human text with ``<``, ``>``
and ``&`` («M&M's <3») must reach Telegram escaped and look as typed.
"""
from __future__ import annotations

import asyncio
import html
import re
from datetime import date
from typing import Any

import pytest
from telegram.constants import ParseMode
from telegram.ext import ApplicationHandlerStop

from family_bot import analytics
from family_bot import bot
from family_bot import draft_expense as draft
from family_bot import look
from family_bot import navigation as nav
from family_bot import payment_reminder as pr
from family_bot.sheet_data import Row
from tests.conftest import FakeSheetsClient, bot_context
from tests.known_bugs.fakes import make_update

NASTY = "M&M's <3 > все"
# Tags the bot may send; anything else with «<» is broken markup.
TAG = re.compile(r"</?(?:b|s|code|blockquote)(?: expandable)?>")
ENTITY = re.compile(r"&(?:amp|lt|gt|quot|#\d+);")


def shown(text: str) -> str:
    """
    What the User sees: the text with tags dropped and entities decoded.

    Fails when the text is not valid Telegram HTML (a raw ``<``, ``>`` or
    ``&`` outside a tag or entity).
    """
    plain = TAG.sub("", text)
    assert "<" not in plain and ">" not in plain, text
    assert "&" not in ENTITY.sub("", plain), text
    return html.unescape(plain)


def run(coro: Any) -> Any:
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------- the formatting module ----------

def test_escape_keeps_text_as_typed() -> None:
    assert look.escape(NASTY) == "M&amp;M's &lt;3 &gt; все"
    assert shown(look.escape(NASTY)) == NASTY


def test_tags_escape_their_text() -> None:
    assert look.bold("a<b") == "<b>a&lt;b</b>"
    assert look.strike("A&B") == "<s>A&amp;B</s>"
    assert look.code("123") == "<code>123</code>"
    assert look.quote(look.bold("x")) == "<blockquote><b>x</b></blockquote>"
    assert look.expandable_quote("x") == "<blockquote expandable>x</blockquote>"
    assert look.field("Что", NASTY) == "Что: <b>M&amp;M's &lt;3 &gt; все</b>"


@pytest.mark.parametrize("share, cells", [
    (0.0, "░░░░░░░░░░"),
    (0.04, "░░░░░░░░░░"),
    (0.41, "████░░░░░░"),
    (1.0, "██████████"),
])
def test_bar(share: float, cells: str) -> None:
    assert look.bar(share) == cells


@pytest.mark.parametrize("n, words", [
    (1, "1 трата"), (3, "3 траты"), (5, "5 трат"), (11, "11 трат"), (21, "21 трата"),
])
def test_expenses_plural(n: int, words: str) -> None:
    assert look.expenses(n) == words


# ---------- human text in every message builder ----------

def _item(**overrides: Any) -> dict:
    item = {"date": "07.11.2026", "category": "Еда <дом>", "amount": 350.0,
            "currency": "дин", "who": "Анна", "comment": NASTY}
    item.update(overrides)
    return item


def test_draft_card_escapes_comment_category_and_transcript() -> None:
    session = draft.session_from_voice([_item()], who="Анна", transcript="M&M's <3")
    seen = shown(draft.card_text(session))
    assert NASTY in seen and "Еда <дом>" in seen and "M&M's <3" in seen


def test_receipt_card_escapes_items_and_merchant() -> None:
    session = draft.session_from_receipt(
        [_item(), _item(comment="Чай & <мёд>")], who="Анна", merchant="Shop & Co <1>")
    seen = shown(draft.card_text(session))
    assert NASTY in seen and "Чай & <мёд>" in seen and "Shop & Co <1>" in seen
    assert "2 траты" in seen


def test_typed_text_card_escapes_the_text() -> None:
    session = draft.session_from_text([_item()], who="Анна", text="кофе <3 & чай")
    assert "кофе <3 & чай" in shown(draft.card_text(session))


def test_draft_save_line_escapes_comment() -> None:
    session = draft.add_draft({}, draft.session_from_voice([_item()], who="Анна", transcript=""))
    outcome = draft.apply_callback("save", session, [], [], FakeSheetsClient())
    assert NASTY in shown(outcome.text or "")


ROWS = [Row(date=date(2026, 11, 5), month="2026-11", category="Еда <дом>", amount=350.0,
            currency="дин", who="Анна & Ко", comment=NASTY)]


def test_month_summary_escapes_category_and_spender() -> None:
    text = analytics.format_month_summary(analytics.month_summary(ROWS, "2026-11"))
    seen = shown(text)
    assert "Еда <дом>" in seen and "Анна & Ко" in seen


def test_last_records_escape_comment_and_category() -> None:
    seen = shown(analytics.format_last_n(ROWS))
    assert NASTY in seen and "Еда <дом>" in seen
    assert "Еда <дом>" in shown(analytics.format_last_n([], category="Еда <дом>"))


def test_payment_reminder_escapes_preset_and_comments() -> None:
    preset = {"name": "Аренда <кв>", "amount": 850.0, "currency": "€", "category": "Жильё & Ко",
              "who": "Оба", "section": "monthly"}
    rows = [Row(date=date(2026, 11, 4), month="2026-11", category="Жильё & Ко", amount=10.0,
                currency="€", who="Анна", comment=NASTY)]
    reminder = pr.new_reminder(pr.due_payments([preset], rows, "2026-11"), "2026-11")
    seen = shown(pr.message_text(reminder))
    assert "Аренда <кв>" in seen and "Жильё & Ко" in seen and NASTY in seen


# ---------- buttons ----------

def test_inline_markup_sets_button_style() -> None:
    markup = nav.inline_markup([[("✅ Сохранить", "a", look.SUCCESS),
                                 ("❌ Отмена", "b", look.DANGER)],
                                [("📅 Дата", "c")]], home=True)
    styles = [[(b.text, b.style) for b in row] for row in markup.inline_keyboard]
    assert styles == [[("✅ Сохранить", "success"), ("❌ Отмена", "danger")],
                      [("📅 Дата", None)],
                      [(nav.HOME_BUTTON, None)]]
    assert markup.inline_keyboard[0][0].to_dict()["style"] == "success"
    assert "style" not in markup.inline_keyboard[1][0].to_dict()


# ---------- the application setting and «нет доступа» ----------

def test_application_sends_html() -> None:
    app = bot.build_application("123:abc")
    assert app.bot.defaults is not None
    assert app.bot.defaults.parse_mode == ParseMode.HTML


def test_no_access_reply_shows_id_monospace() -> None:
    update = make_update("привет", user_id=999)
    context = bot_context(allowed_users={111: "Анна"})
    with pytest.raises(ApplicationHandlerStop):
        run(bot.enforce_allowlist(update, context))
    [(text, kw)] = update.message.replies
    assert "<code>999</code>" in text
    assert "parse_mode" not in kw
    shown(text)
