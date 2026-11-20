"""Payment reminder on the 5th and 20th (issue #89): no network, fake clock, job queue and bot."""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from types import SimpleNamespace
from typing import Any, Callable, Optional

import pytest

from family_bot import bot
from family_bot import clock
from family_bot import config
from family_bot import errors
from family_bot import payment_reminder as pr
from family_bot import payment_reminder_bot as prb
from family_bot import regular
from family_bot import undo
from family_bot.sheet_data import Row
from family_bot.sheets import DATA_HEADERS
from tests.conftest import FakeSheetsClient, bot_context, settings_of
from tests.known_bugs.fakes import FakeMessage, FakeQuery
from tests.test_monthly_summary import FakeJobQueue, fake_bot_app, with_sheets

MONTH = "2026-11"
RENT = {"name": "Аренда", "amount": 850.0, "currency": "€", "category": "Жильё",
        "who": "Оба", "section": "monthly"}
BILLS = {"name": "Коммуналка", "amount": None, "currency": "дин", "category": "Счета",
         "who": "Анна", "section": "monthly"}
SAUNA = {"name": "Баня", "amount": 3850.0, "currency": "дин", "category": "Отдых",
         "who": "Оба", "section": "oneoff"}


def row(day: int, category: str, amount: float, comment: str, month: str = MONTH,
        currency: str = "дин") -> Row:
    year, number = (int(p) for p in month.split("-"))
    return Row(date=date(year, number, day), month=month, category=category,
               amount=amount, currency=currency, who="Анна", comment=comment)


def run(coro: Any) -> Any:
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------- which payments are due (no Telegram) ----------

@pytest.mark.parametrize("comment", ["Аренда", "  аренда ", "АРЕНДА"])
def test_payment_with_its_name_in_comment_this_month_is_paid(comment: str) -> None:
    rows = [row(3, "Прочее", 850, comment)]
    assert pr.due_payments([RENT], rows, MONTH) == []


def test_payment_entered_last_month_or_with_other_comment_is_due() -> None:
    rows = [row(3, "Жильё", 850, "Аренда", month="2026-10"),
            row(4, "Жильё", 850, "аренда за ноябрь")]
    [due] = pr.due_payments([RENT], rows, MONTH)
    assert due.preset is RENT


def test_due_payment_lists_same_category_rows_of_the_month() -> None:
    same = row(4, "Жильё", 800, "квартира", currency="€")
    rows = [same, row(5, "Жильё", 1, "старое", month="2026-10"), row(6, "Еда", 5, "кофе")]
    [due] = pr.due_payments([RENT], rows, MONTH)
    assert due.same_category == (same,)


def test_oneoff_presets_are_never_due() -> None:
    assert pr.due_payments([SAUNA], [], MONTH) == []


def test_preset_without_amount_is_due() -> None:
    [due] = pr.due_payments([BILLS], [], MONTH)
    assert due.preset["amount"] is None


def test_remind_flag_false_leaves_the_preset_out() -> None:
    assert pr.due_payments([{**RENT, "remind": False}], [], MONTH) == []
    assert len(pr.due_payments([{**RENT, "remind": True}], [], MONTH)) == 1


GRID_HEAD = ["название", "евро", "категория", "кто"]


def test_without_remind_column_presets_have_no_flag_and_all_are_due() -> None:
    grid = [GRID_HEAD, ["Аренда", "850", "Жильё", "Оба"], ["Телефон", "20", "Связь", "Оба"]]
    presets = [{**p, "section": "monthly"} for p in regular.parse_regular(grid)["monthly"]]
    assert all("remind" not in p for p in presets)
    assert len(pr.due_payments(presets, [], MONTH)) == 2


def test_remind_column_keeps_only_rows_with_yes() -> None:
    grid = [[*GRID_HEAD, "напоминать"],
            ["Аренда", "850", "Жильё", "Оба", " Да "],
            ["Подписка", "5", "Связь", "Оба", "нет"],
            ["Телефон", "20", "Связь", "Оба", ""]]
    presets = [{**p, "section": "monthly"} for p in regular.parse_regular(grid)["monthly"]]
    assert [p["remind"] for p in presets] == [True, False, False]
    assert [d.preset["name"] for d in pr.due_payments(presets, [], MONTH)] == ["Аренда"]


# ---------- message text and buttons ----------

def reminder(*presets: dict, rows: Optional[list[Row]] = None) -> pr.Reminder:
    return pr.new_reminder(pr.due_payments(list(presets), rows or [], MONTH), MONTH)


def test_message_lists_payments_with_same_category_rows() -> None:
    rem = reminder(RENT, BILLS, rows=[row(4, "Жильё", 800, "квартира", currency="€")])
    text = pr.message_text(rem)
    assert text.splitlines()[0] == "🔔 Ноябрь 2026: ещё не внесены платежи"
    assert "• Аренда — 850 € (Жильё)" in text
    assert "   уже есть: 04.11.2026 — 800 € — квартира" in text
    assert "• Коммуналка — сумма не задана (Счета)" in text
    assert text.count("уже есть") == 1  # no same-Category rows → no «уже есть» line


def test_each_payment_has_pay_and_already_buttons() -> None:
    rem = reminder(RENT, BILLS)
    labels = [[label for label, _ in r] for r in pr.buttons(rem)]
    assert labels == [["⚡ Внести: Аренда", pr.ALREADY_LABEL],
                      ["⚡ Внести: Коммуналка", pr.ALREADY_LABEL]]
    datas = [cb for r in pr.buttons(rem) for _, cb in r]
    assert all(len(cb.encode()) <= 64 for cb in datas)
    assert pr.parse_callback(datas[1]) == (rem.id, 0, pr.ACTION_ALREADY)


def test_closed_lines_leave_the_message_and_the_last_one_closes_it() -> None:
    rem = reminder(RENT, BILLS)
    assert pr.take(rem, 0) is not None
    assert pr.take(rem, 0) is None  # a second tap finds nothing
    assert "Аренда" not in pr.message_text(rem)
    assert len(pr.buttons(rem)) == 1
    pr.take(rem, 1)
    assert pr.message_text(rem) == pr.ALL_CLOSED_TEXT.format(month="Ноябрь 2026")
    assert pr.buttons(rem) == []


def test_record_is_today_from_sheet_values_with_name_as_comment() -> None:
    record = pr.record(RENT, 850.0, "€", "20.11.2026")
    assert (record.date, record.category, record.amount, record.currency, record.who,
            record.comment, record.large) == (
        "20.11.2026", "Жильё", 850.0, "€", "Оба", "Аренда", False)


@pytest.mark.parametrize("bad", ["", "pr:x", "pr:ab:zz:pay", "pr:ab:0:other"])
def test_bad_callback_data_is_none(bad: str) -> None:
    assert pr.parse_callback(bad) is None


# ---------- settings and scheduling ----------

ENV = {"ALLOWED_USERS": "111:Ann"}


def test_reminder_hour_default_and_override() -> None:
    assert config.load(ENV).reminder_hour == config.DEFAULT_REMINDER_HOUR == 10
    assert config.load({**ENV, config.REMINDER_HOUR_ENV: "8"}).reminder_hour == 8


@pytest.mark.parametrize("raw", ["24", "утром"])
def test_bad_reminder_hour_keeps_default(raw: str) -> None:
    cfg = config.load({**ENV, config.REMINDER_HOUR_ENV: raw})
    assert cfg.reminder_hour == config.DEFAULT_REMINDER_HOUR
    assert any(config.REMINDER_HOUR_ENV in p and " — " in p for p in cfg.problems)


def test_schedule_runs_on_5th_and_20th_at_family_hour(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(clock.TZ_ENV, "Asia/Tokyo")
    queue = FakeJobQueue()
    prb.schedule(queue, chat_id=-100, hour=10, load_inputs=lambda: ([], []))  # type: ignore[arg-type]
    assert sorted(job["day"] for job in queue.monthly) == [5, 20]
    for job in queue.monthly:
        assert job["chat_id"] == -100 and job["callback"] is prb.send_reminder
        assert job["when"].hour == 10 and str(job["when"].tzinfo) == "Asia/Tokyo"
    assert len({job["name"] for job in queue.monthly}) == 2


def reminder_jobs(queue: FakeJobQueue) -> list[dict[str, Any]]:
    return [j for j in queue.monthly if j["callback"] is prb.send_reminder]


def test_post_init_schedules_reminder_reading_sheets_at_send_time(
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_CHAT_ID", "-100")
    monkeypatch.setenv(config.REMINDER_HOUR_ENV, "11")
    with_sheets(monkeypatch, FakeSheetsClient(categories=["Еда"]))
    queue = FakeJobQueue()
    app = fake_bot_app(queue)
    run(bot.post_init(app))  # type: ignore[arg-type]
    jobs = reminder_jobs(queue)
    assert len(jobs) == 2 and all(j["when"].hour == 11 for j in jobs)
    app.bot_data[config.KEY].sheets = FakeSheetsClient(
        data_values=[DATA_HEADERS, ["03.11.2026", MONTH, "Еда", "1200", "дин", "Ann", ""]],
        regular_values=[GRID_HEAD, ["Аренда", "850", "Жильё", "Оба"]])
    presets, rows = jobs[0]["data"]()
    assert [p["name"] for p in presets] == ["Аренда"]
    assert [r.amount for r in rows] == [1200.0]


@pytest.mark.parametrize("env", [{"DASHBOARD_CHAT_ID": "-100"}, {}], ids=["no sheets", "no chat"])
def test_post_init_without_chat_or_sheets_has_no_reminder(
        env: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DASHBOARD_CHAT_ID", raising=False)
    monkeypatch.delenv("SHEET_NAME", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    queue = FakeJobQueue()
    run(bot.post_init(fake_bot_app(queue)))  # type: ignore[arg-type]
    assert reminder_jobs(queue) == []


# ---------- the job ----------

class FakeBot:
    """Records sent and edited messages."""

    def __init__(self, error: Exception | None = None) -> None:
        self.sent: list[tuple[int, str, Any]] = []
        self.edited: list[dict[str, Any]] = []
        self.error = error

    async def send_message(self, chat_id: int, text: str, **kw: Any) -> SimpleNamespace:
        if self.error is not None:
            raise self.error
        self.sent.append((chat_id, text, kw.get("reply_markup")))
        return SimpleNamespace(chat_id=chat_id, message_id=77)

    async def edit_message_text(self, text: str, **kw: Any) -> None:
        self.edited.append({"text": text, **kw})


def job_context(load: Callable[[], tuple[list[dict], list[Row]]], telegram: FakeBot,
                bot_data: Optional[dict] = None) -> SimpleNamespace:
    return SimpleNamespace(bot=telegram, bot_data=bot_data if bot_data is not None else {},
                           job=SimpleNamespace(chat_id=-100, data=load))


@pytest.fixture
def november(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clock, "now", lambda: datetime(2026, 11, 20, 10, 0))


def test_job_sends_reminder_and_keeps_it_in_bot_data(november: None) -> None:
    telegram, bot_data = FakeBot(), {}
    run(prb.send_reminder(job_context(lambda: ([RENT, BILLS], []), telegram, bot_data)))  # type: ignore[arg-type]
    [(chat_id, text, markup)] = telegram.sent
    assert chat_id == -100 and "Аренда" in text and "Коммуналка" in text
    assert len(markup.inline_keyboard) == 2
    [rem] = pr.reminders(bot_data).values()
    assert (rem.chat_id, rem.message_id) == (-100, 77)


def test_job_sends_nothing_when_all_are_paid(november: None) -> None:
    telegram = FakeBot()
    rows = [row(1, "Жильё", 850, "Аренда"), row(2, "Счета", 4000, "коммуналка")]
    run(prb.send_reminder(job_context(lambda: ([RENT, BILLS], rows), telegram)))  # type: ignore[arg-type]
    assert telegram.sent == []


def test_job_failure_is_logged_not_raised(november: None, caplog: pytest.LogCaptureFixture) -> None:
    def broken() -> tuple[list[dict], list[Row]]:
        raise RuntimeError("sheet down")

    with caplog.at_level(logging.ERROR):
        run(prb.send_reminder(job_context(broken, FakeBot())))  # type: ignore[arg-type]
    assert "sheet down" in caplog.text


# ---------- buttons ----------

USER_ID = 111


def tap(data: str, user_id: int = USER_ID) -> tuple[SimpleNamespace, FakeQuery]:
    query = FakeQuery(data, message=FakeMessage("напоминание"))
    update = SimpleNamespace(callback_query=query, effective_message=query.message,
                             effective_user=SimpleNamespace(id=user_id), message=None)
    return update, query


def sent_reminder(sheets: Any, *presets: dict) -> tuple[SimpleNamespace, pr.Reminder]:
    """Bot context after the job sent a reminder for ``presets``."""
    ctx = bot_context(sheets=sheets)
    ctx.bot = FakeBot()
    rem = reminder(*presets)
    rem.chat_id, rem.message_id = -100, 77
    pr.add(ctx.bot_data, rem)
    return ctx, rem


def callback(rem: pr.Reminder, index: int, action: str) -> str:
    return f"{pr.CALLBACK_PREFIX}{rem.id}:{index}:{action}"


def test_pay_with_amount_saves_at_once_with_undo_for_presser(november: None) -> None:
    sheets = FakeSheetsClient(data_values=[DATA_HEADERS])
    ctx, rem = sent_reminder(sheets, RENT, BILLS)
    update, query = tap(callback(rem, 0, pr.ACTION_PAY))
    run(prb.on_button(update, ctx))  # type: ignore[arg-type]
    [saved] = sheets.appended_rows
    assert saved[:7] == ["20.11.2026", MONTH, "Жильё", 850.0, "€", "Оба", "Аренда"]
    [(text, kw)] = query.message.replies
    assert text == "✅ Сохранено: Аренда — 850 € · 20.11.2026"
    [[button]] = kw["reply_markup"].inline_keyboard
    assert button.text == undo.UNDO_LABEL
    assert isinstance(ctx.user_data[undo.KEY_LAST_SAVE], undo.LastSave)
    [edit] = ctx.bot.edited
    assert "Аренда" not in edit["text"] and "Коммуналка" in edit["text"]
    assert (edit["chat_id"], edit["message_id"]) == (-100, 77)


def test_last_line_closed_drops_buttons_and_the_reminder(november: None) -> None:
    ctx, rem = sent_reminder(FakeSheetsClient(data_values=[DATA_HEADERS]), RENT)
    run(prb.on_button(*tap(callback(rem, 0, pr.ACTION_PAY))[:1], ctx))  # type: ignore[arg-type]
    [edit] = ctx.bot.edited
    assert edit["text"].startswith("🔔 Ноябрь 2026") and edit.get("reply_markup") is None
    assert pr.reminders(ctx.bot_data) == {}


def test_already_entered_removes_the_line_and_writes_nothing(november: None) -> None:
    sheets = FakeSheetsClient(data_values=[DATA_HEADERS])
    ctx, rem = sent_reminder(sheets, RENT, BILLS)
    update, query = tap(callback(rem, 1, pr.ACTION_ALREADY))
    run(prb.on_button(update, ctx))  # type: ignore[arg-type]
    assert sheets.appended_rows == [] and query.message.replies == []
    [edit] = ctx.bot.edited
    assert "Коммуналка" not in edit["text"] and "Аренда" in edit["text"]


def test_second_tap_on_closed_line_writes_nothing_again(november: None) -> None:
    sheets = FakeSheetsClient(data_values=[DATA_HEADERS])
    ctx, rem = sent_reminder(sheets, RENT, BILLS)
    run(prb.on_button(tap(callback(rem, 0, pr.ACTION_PAY))[0], ctx))  # type: ignore[arg-type]
    update, query = tap(callback(rem, 0, pr.ACTION_PAY), user_id=222)
    run(prb.on_button(update, ctx))  # type: ignore[arg-type]
    assert len(sheets.appended_rows) == 1
    assert query.answers == [(pr.CLOSED_TEXT,)]


def test_failed_save_puts_the_line_back(november: None) -> None:
    class Broken(FakeSheetsClient):
        def append_data_row(self, row: list) -> None:
            raise RuntimeError("quota")

    ctx, rem = sent_reminder(Broken(data_values=[DATA_HEADERS]), RENT)
    update, query = tap(callback(rem, 0, pr.ACTION_PAY))
    run(prb.on_button(update, ctx))  # type: ignore[arg-type]
    assert [t for t, _ in query.message.replies] == [errors.SAVE_FAILED]
    assert rem.open == {0} and ctx.bot.edited == []


def test_stale_button_after_restart_explains_in_an_alert() -> None:
    ctx = bot_context(sheets=FakeSheetsClient())  # bot_data wiped by the restart
    update, query = tap(f"{pr.CALLBACK_PREFIX}dead:0:{pr.ACTION_PAY}")
    run(prb.on_button(update, ctx))  # type: ignore[arg-type]
    assert query.answers == [(pr.STALE_TEXT,)]
    assert pr.STALE_TEXT == "Напоминание устарело после обновления бота"
    assert query.edits == [("markup", {"reply_markup": None})]


def test_button_handler_is_wired_after_the_allowlist() -> None:
    app = bot.build_application("123:abc")
    patterns = [getattr(h, "pattern", None) for h in app.handlers[0]]
    assert any(p is not None and p.pattern == f"^{pr.CALLBACK_PREFIX}" for p in patterns)
    assert prb.AMOUNT_REPLY in app.handlers[-1]  # before the Draft amount text, same group
    assert app.handlers[-1].index(prb.AMOUNT_REPLY) < app.handlers[-1].index(bot.DRAFT_AMOUNT_TEXT)


# ---------- pay without an amount: ForceReply ----------

class ReplyMessage(FakeMessage):
    """A User's text that answers a bot message."""

    def __init__(self, text: str, reply_to: SimpleNamespace) -> None:
        super().__init__(text)
        self.reply_to_message = reply_to
        self.sent: list[SimpleNamespace] = []

    async def reply_text(self, text: str, **kw: Any) -> Any:  # type: ignore[override]
        await super().reply_text(text, **kw)
        sent = SimpleNamespace(chat_id=-100, message_id=900 + len(self.replies), text=text)
        self.sent.append(sent)
        return sent


class PromptQueryMessage(FakeMessage):
    async def reply_text(self, text: str, **kw: Any) -> Any:  # type: ignore[override]
        await super().reply_text(text, **kw)
        return SimpleNamespace(chat_id=-100, message_id=500, text=text)


def ask_amount(ctx: SimpleNamespace, rem: pr.Reminder, index: int) -> SimpleNamespace:
    """Tap «⚡ Внести» on a payment without an amount; return the prompt message."""
    query = FakeQuery(callback(rem, index, pr.ACTION_PAY), message=PromptQueryMessage("напоминание"))
    update = SimpleNamespace(callback_query=query, effective_message=query.message,
                             effective_user=SimpleNamespace(id=USER_ID), message=None)
    run(prb.on_button(update, ctx))  # type: ignore[arg-type]
    [(text, kw)] = query.message.replies
    assert pr.PROMPT_MARK in text and kw["reply_markup"].force_reply
    return SimpleNamespace(chat_id=-100, message_id=500, text=text)


def answer(ctx: SimpleNamespace, prompt: SimpleNamespace, text: str,
           user_id: int = USER_ID) -> ReplyMessage:
    msg = ReplyMessage(text, prompt)
    update = SimpleNamespace(message=msg, effective_message=msg, callback_query=None,
                             effective_user=SimpleNamespace(id=user_id))
    with pytest.raises(bot.ApplicationHandlerStop):
        run(prb.on_amount_reply(update, ctx))  # type: ignore[arg-type]
    return msg


def test_pay_without_amount_asks_by_force_reply_then_saves(november: None) -> None:
    sheets = FakeSheetsClient(data_values=[DATA_HEADERS])
    ctx, rem = sent_reminder(sheets, RENT, BILLS)
    prompt = ask_amount(ctx, rem, 1)
    assert sheets.appended_rows == [] and rem.open == {0, 1}
    msg = answer(ctx, prompt, "4 200")
    [saved] = sheets.appended_rows
    assert saved[2:7] == ["Счета", 4200.0, "дин", "Анна", "Коммуналка"]
    [(text, kw)] = msg.replies
    assert text == "✅ Сохранено: Коммуналка — 4 200 дин · 20.11.2026"
    assert kw["reply_markup"].inline_keyboard[0][0].text == undo.UNDO_LABEL
    assert rem.open == {0}


def test_bad_amount_asks_again_with_the_same_checks(november: None) -> None:
    sheets = FakeSheetsClient(data_values=[DATA_HEADERS])
    ctx, rem = sent_reminder(sheets, BILLS)
    prompt = ask_amount(ctx, rem, 0)
    msg = answer(ctx, prompt, "ноль")
    [(text, kw)] = msg.replies
    assert pr.PROMPT_MARK in text and kw["reply_markup"].force_reply
    assert sheets.appended_rows == []
    answer(ctx, msg.sent[0], "12,5 €")  # the retry prompt also takes the answer
    assert sheets.appended_rows[0][3:5] == [12.5, "€"]


def test_other_users_answer_is_not_taken(november: None) -> None:
    sheets = FakeSheetsClient(data_values=[DATA_HEADERS])
    ctx, rem = sent_reminder(sheets, BILLS)
    prompt = ask_amount(ctx, rem, 0)
    msg = answer(ctx, prompt, "4200", user_id=222)
    assert sheets.appended_rows == [] and msg.replies[0][0] == pr.NOT_YOURS_TEXT


def test_answer_to_a_prompt_after_restart_is_stale() -> None:
    ctx = bot_context(sheets=FakeSheetsClient())
    prompt = SimpleNamespace(chat_id=-100, message_id=500, text=pr.PROMPT_MARK)
    msg = answer(ctx, prompt, "4200")
    assert msg.replies[0][0] == pr.STALE_TEXT


def test_amount_reply_filter_takes_only_answers_to_the_prompt() -> None:
    def message(reply_text: Optional[str], is_bot: bool = True) -> Any:
        reply = None if reply_text is None else SimpleNamespace(
            text=reply_text, from_user=SimpleNamespace(is_bot=is_bot))
        return SimpleNamespace(reply_to_message=reply)

    check = prb.REPLY_TO_PROMPT.filter
    assert check(message(f"💵 … {pr.PROMPT_MARK}"))  # type: ignore[arg-type]
    assert not check(message(None))  # type: ignore[arg-type]
    assert not check(message("другое"))  # type: ignore[arg-type]
    assert not check(message(pr.PROMPT_MARK, is_bot=False))  # type: ignore[arg-type]


def test_settings_of_context_has_reminder_hour() -> None:
    assert settings_of(bot_context()).reminder_hour == config.DEFAULT_REMINDER_HOUR
