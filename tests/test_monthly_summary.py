"""Monthly summary on the 1st (issue #36, plan 4.2, F4): no network, fake clock and job queue."""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from types import SimpleNamespace
from typing import Any, Callable

import pytest
from telegram.error import NetworkError

from family_bot import bot
from family_bot import clock
from family_bot import config
from family_bot import monthly_summary as ms
from family_bot.sheet_data import Row
from family_bot.sheets import DATA_HEADERS
from tests.conftest import FakeSheetsClient

ENV = {"ALLOWED_USERS": "111:Ann"}

ROWS = [
    Row(date=date(2026, 11, 3), month="2026-11", category="Еда",
        amount=1200.0, currency="дин", who="Ann", comment=""),
    Row(date=date(2026, 12, 1), month="2026-12", category="Кафе",
        amount=500.0, currency="дин", who="Bob", comment=""),
]


def run(coro: Any) -> Any:
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------- previous month ----------

@pytest.mark.parametrize("today, expected", [
    (datetime(2026, 12, 1, 9), "2026-11"),
    (datetime(2027, 1, 1, 9), "2026-12"),
])
def test_previous_month(today: datetime, expected: str) -> None:
    assert ms.previous_month(today) == expected


# ---------- settings ----------

def test_chat_id_is_read_from_dashboard_chat_id() -> None:
    cfg = config.load({**ENV, "DASHBOARD_CHAT_ID": "-1001234"})
    assert cfg.summary_chat_id == -1001234
    assert not any("DASHBOARD_CHAT_ID" in p for p in cfg.problems)


def test_missing_chat_id_is_one_setup_line() -> None:
    cfg = config.load(ENV)
    assert cfg.summary_chat_id is None
    lines = [p for p in cfg.problems if "DASHBOARD_CHAT_ID" in p]
    assert len(lines) == 1 and " — " in lines[0] and "\n" not in lines[0]


def test_bad_chat_id_is_one_setup_line() -> None:
    cfg = config.load({**ENV, "DASHBOARD_CHAT_ID": "my chat"})
    assert cfg.summary_chat_id is None
    assert any("DASHBOARD_CHAT_ID" in p and " — " in p for p in cfg.problems)


def test_summary_hour_default_and_override() -> None:
    assert config.load(ENV).summary_hour == config.DEFAULT_SUMMARY_HOUR
    assert config.load({**ENV, config.SUMMARY_HOUR_ENV: "7"}).summary_hour == 7


@pytest.mark.parametrize("raw", ["24", "-1", "утром"])
def test_bad_summary_hour_keeps_default(raw: str) -> None:
    cfg = config.load({**ENV, config.SUMMARY_HOUR_ENV: raw})
    assert cfg.summary_hour == config.DEFAULT_SUMMARY_HOUR
    assert any(config.SUMMARY_HOUR_ENV in p and " — " in p for p in cfg.problems)


# ---------- scheduling ----------

class FakeJobQueue:
    """Records run_monthly calls."""

    def __init__(self) -> None:
        self.monthly: list[dict[str, Any]] = []

    def run_monthly(self, callback: Callable[..., Any], **kwargs: Any) -> None:
        self.monthly.append({"callback": callback, **kwargs})


def test_schedule_runs_on_the_first_at_family_hour(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(clock.TZ_ENV, "Asia/Tokyo")
    queue = FakeJobQueue()
    ms.schedule(queue, chat_id=-100, hour=8, load_rows=lambda: [])  # type: ignore[arg-type]
    (job,) = queue.monthly
    assert job["day"] == 1
    assert job["chat_id"] == -100
    assert job["when"].hour == 8 and job["when"].minute == 0
    assert str(job["when"].tzinfo) == "Asia/Tokyo"
    assert job["callback"] is ms.send_summary


def fake_bot_app(queue: FakeJobQueue) -> SimpleNamespace:
    class Bot:
        async def set_my_commands(self, commands: list[Any]) -> bool:
            return True

    return SimpleNamespace(bot_data={}, bot=Bot(), job_queue=queue)


def test_post_init_schedules_summary_when_chat_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_CHAT_ID", "-100")
    with_sheets(monkeypatch, FakeSheetsClient(categories=["Еда"]))
    monkeypatch.setenv(config.SUMMARY_HOUR_ENV, "10")
    queue = FakeJobQueue()
    run(bot.post_init(fake_bot_app(queue)))  # type: ignore[arg-type]
    (job,) = queue.monthly
    assert job["chat_id"] == -100 and job["when"].hour == 10


def test_post_init_without_sheets_skips_job(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_CHAT_ID", "-100")
    monkeypatch.delenv("SHEET_NAME", raising=False)
    queue = FakeJobQueue()
    run(bot.post_init(fake_bot_app(queue)))  # type: ignore[arg-type]
    assert queue.monthly == []  # the Sheets problem line already says what to fix


def test_bad_time_zone_does_not_stop_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(clock.TZ_ENV, "Mars/Base")
    queue = FakeJobQueue()
    ms.schedule(queue, chat_id=-100, hour=9, load_rows=lambda: [])  # type: ignore[arg-type]
    assert str(queue.monthly[0]["when"].tzinfo) == clock.DEFAULT_TZ


def test_post_init_without_chat_skips_job_and_logs_one_line(
        monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    monkeypatch.delenv("DASHBOARD_CHAT_ID", raising=False)
    queue = FakeJobQueue()
    run(bot.post_init(fake_bot_app(queue)))  # type: ignore[arg-type]
    assert queue.monthly == []
    out = [line for line in caplog.messages if "DASHBOARD_CHAT_ID" in line]
    assert len(out) == 1 and out[0].startswith("⚠️")


def with_sheets(monkeypatch: pytest.MonkeyPatch, sheets: Any) -> None:
    """Make post_init's config.load return a Config with this Sheets client."""
    real_load = config.load
    monkeypatch.setattr(config, "load", lambda env: real_load(env, open_sheets=lambda c, n: sheets))
    monkeypatch.setattr(config, "_client_email", lambda creds, problems: "bot@example.test")
    monkeypatch.setenv("GOOGLE_CREDS_PATH", "creds.json")
    monkeypatch.setenv("SHEET_NAME", "Family")


def test_post_init_job_reads_rows_from_config_sheets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_CHAT_ID", "-100")
    with_sheets(monkeypatch, FakeSheetsClient(categories=["Еда"]))
    queue = FakeJobQueue()
    app = fake_bot_app(queue)
    run(bot.post_init(app))  # type: ignore[arg-type]
    app.bot_data[config.KEY].sheets = FakeSheetsClient(
        data_values=[DATA_HEADERS, ["03.11.2026", "2026-11", "Еда", "1200", "дин", "Ann", ""]])
    rows = queue.monthly[0]["data"]()
    assert [r.amount for r in rows] == [1200.0]


# ---------- the job itself ----------

class FakeTelegram:
    def __init__(self, error: Exception | None = None) -> None:
        self.sent: list[tuple[int, str]] = []
        self.error = error

    async def send_message(self, chat_id: int, text: str) -> None:
        if self.error is not None:
            raise self.error
        self.sent.append((chat_id, text))


def job_context(load_rows: Callable[[], list[Row]],
                telegram: FakeTelegram) -> SimpleNamespace:
    return SimpleNamespace(bot=telegram, job=SimpleNamespace(chat_id=-100, data=load_rows))


def test_job_sends_previous_month_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clock, "now", lambda: datetime(2026, 12, 1, 9, 0))
    telegram = FakeTelegram()
    run(ms.send_summary(job_context(lambda: ROWS, telegram)))  # type: ignore[arg-type]
    ((chat_id, text),) = telegram.sent
    assert chat_id == -100
    assert text.startswith("📅 Ноябрь 2026")
    assert "Еда" in text and "Кафе" not in text  # December rows stay out


def test_job_loads_rows_off_the_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clock, "now", lambda: datetime(2026, 12, 1, 9, 0))
    loops: list[bool] = []

    def load_rows() -> list[Row]:
        try:
            asyncio.get_running_loop()
            loops.append(True)
        except RuntimeError:
            loops.append(False)
        return ROWS

    run(ms.send_summary(job_context(load_rows, FakeTelegram())))  # type: ignore[arg-type]
    assert loops == [False]


def test_job_failure_is_logged_not_raised(caplog: pytest.LogCaptureFixture) -> None:
    def broken() -> list[Row]:
        raise RuntimeError("sheet down")

    telegram = FakeTelegram()
    with caplog.at_level(logging.ERROR):
        run(ms.send_summary(job_context(broken, telegram)))  # type: ignore[arg-type]
    assert telegram.sent == []
    assert "sheet down" in caplog.text


def test_job_telegram_error_is_logged_not_raised(caplog: pytest.LogCaptureFixture) -> None:
    telegram = FakeTelegram(error=NetworkError("telegram down"))
    with caplog.at_level(logging.ERROR):
        run(ms.send_summary(job_context(lambda: ROWS, telegram)))  # type: ignore[arg-type]
    assert "telegram down" in caplog.text
