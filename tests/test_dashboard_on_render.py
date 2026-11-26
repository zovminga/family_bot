"""Dashboard build inside the bot (issue #119): no network, fake job queue and Telegram."""
from __future__ import annotations

import asyncio
import csv
import json
import logging
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from family_bot import bot
from family_bot import clock
from family_bot import config
from family_bot import dashboard_job as dj
from family_bot.dashboard import build_dashboard as bd
from family_bot.dashboard import failure_alert as fa
from tests.conftest import FakeSheetsClient, bot_context
from tests.known_bugs.fakes import FakeMessage

TOKEN = "123456:SECRET-token-value"


def run(coro: Any) -> Any:
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in (bd.FILES_DIR_ENV, "BASE_CURRENCY", "DASHBOARD_FROM_MONTH",
                 "DASHBOARD_SPENDER_ALIASES", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(fa, "ENV_PATH", tmp_path / ".env")
    monkeypatch.setenv("BOT_TOKEN", TOKEN)
    monkeypatch.setenv("DASHBOARD_CHAT_ID", "4242")


# ---------- household files folder ----------

def test_household_files_default_to_dashboard_folder() -> None:
    assert bd.household_file("theme_keywords.json") == bd.DASHBOARD_DIR / "theme_keywords.json"


def test_household_files_folder_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(bd.FILES_DIR_ENV, str(tmp_path))
    assert bd.household_file("manual_adjustments.csv") == tmp_path / "manual_adjustments.csv"


def test_relative_household_folder_is_from_repo_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(bd.FILES_DIR_ENV, "private")
    assert bd.household_file("x.json") == bd.BOT_DIR / "private" / "x.json"


def _write_csv(path: Path, rows: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        csv.writer(fh).writerows(rows)


def test_build_merges_adjustments_from_files_folder(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    headers = ["Date", "Month", "Category", "Amount", "Currency", "Spender", "Comment"]
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    _write_csv(secrets / "manual_adjustments.csv",
               [headers, ["01.11.2026", "2026-11", "Rent", "900", "€", "Ann", "аренда"]])
    _write_csv(tmp_path / "data.csv",
               [headers, ["05.11.2026", "2026-11", "Food", "10", "€", "Ann", "кофе"]])
    monkeypatch.setenv(bd.FILES_DIR_ENV, str(secrets))
    monkeypatch.setattr(bd, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setattr(bd, "DASHBOARD_DIR", tmp_path)  # nothing to read here
    monkeypatch.setattr(bd, "fetch_rates", lambda base: {"EUR": 1.0})
    monkeypatch.setattr(bd, "get_chartjs", lambda: "/* chart.js stub */")

    out = bd.build(base_iso="EUR", model="m", use_llm=False, csv_path=str(tmp_path / "data.csv"))

    html = out.read_text(encoding="utf-8")
    assert '"comment": "аренда"' in html and '"comment": "кофе"' in html


def test_build_time_follows_family_zone(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    headers = ["Date", "Month", "Category", "Amount", "Currency", "Spender", "Comment"]
    _write_csv(tmp_path / "data.csv",
               [headers, ["05.11.2026", "2026-11", "Food", "10", "€", "Ann", "кофе"]])
    monkeypatch.setattr(bd, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setattr(bd, "DASHBOARD_DIR", tmp_path)
    monkeypatch.setattr(bd, "fetch_rates", lambda base: {"EUR": 1.0})
    monkeypatch.setattr(bd, "get_chartjs", lambda: "/* chart.js stub */")
    # Render runs in UTC: the stamp must be family time, not the server clock.
    monkeypatch.setattr(clock, "now", lambda: datetime(2026, 12, 1, 9, 0))

    out = bd.build(base_iso="EUR", model="m", use_llm=False, csv_path=str(tmp_path / "data.csv"))

    html = out.read_text(encoding="utf-8")
    assert '"generated_at": "2026-12-01 09:00"' in html
    assert '"as_of": "2026-12-01"' in html


# ---------- build with settings from the environment ----------

@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, list[Any]]:
    """Replace build and publish with recorders."""
    seen: dict[str, list[Any]] = {"build": [], "publish": []}
    out = tmp_path / "out.html"
    monkeypatch.setattr(bd, "build", lambda **kw: seen["build"].append(kw) or out)
    monkeypatch.setattr(bd, "publish_to_telegram", lambda path: seen["publish"].append(path))
    return seen


def test_env_build_publishes_with_household_settings(
        calls: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BASE_CURRENCY", "RSD")
    monkeypatch.setenv("DASHBOARD_FROM_MONTH", "2025-10")
    monkeypatch.setenv("DASHBOARD_SPENDER_ALIASES", "ann=Аня")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key")

    assert bd.build_from_env() is True

    (kw,) = calls["build"]
    assert kw["base_iso"] == "RSD" and kw["from_month"] == "2025-10"
    assert kw["spender_aliases"] == {"ann": "Аня"}
    assert kw["use_llm"] is True and kw["provider"] == "anthropic"
    assert kw["csv_path"] is None
    assert len(calls["publish"]) == 1


def test_env_build_without_claude_key_uses_rules(calls: dict[str, list[Any]]) -> None:
    assert bd.build_from_env() is True
    assert calls["build"][0]["use_llm"] is False
    assert calls["build"][0]["base_iso"] == "EUR"


def test_env_build_reads_theme_keywords_from_files_folder(
        calls: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "theme_keywords.json").write_text(
        json.dumps({"Кофе": ["corner cafe"]}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv(bd.FILES_DIR_ENV, str(tmp_path))
    assert bd.build_from_env() is True
    rules = dict(calls["build"][0]["theme_rules"])
    assert "corner cafe" in rules["Кофе"]


class FakeSender:
    def __init__(self) -> None:
        self.texts: list[str] = []

    def __call__(self, token: str, chat_id: str, text: str) -> None:
        self.texts.append(text)


def test_env_build_failure_alerts_and_does_not_raise(
        monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    def broken_build(**_kw: Any) -> Path:
        raise RuntimeError("Bob 1500 аренда")

    sender = FakeSender()
    monkeypatch.setattr(fa, "post_message", sender)
    monkeypatch.setattr(bd, "build", broken_build)

    assert bd.build_from_env() is False

    (text,) = sender.texts
    assert "сборка: RuntimeError" in text and "Bob" not in text
    # The bot's alert points at the bot log and the command, not the Mac files.
    assert "/build_dashboard" in text and "launchd.log" not in text
    assert any("сборка" in r.getMessage() for r in caplog.records)


def test_env_build_bad_first_month_alerts(
        calls: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_FROM_MONTH", "2025/10")
    sender = FakeSender()
    monkeypatch.setattr(fa, "post_message", sender)
    assert bd.build_from_env() is False
    assert calls["build"] == []
    assert "ValueError" in sender.texts[0]


def test_local_alert_still_points_at_run_sh() -> None:
    text = fa.failure_text("сборка: RuntimeError", TOKEN)
    assert "launchd.log" in text and "run.sh" in text


# ---------- the job ----------

class FakeJobQueue:
    def __init__(self) -> None:
        self.monthly: list[dict[str, Any]] = []
        self.once: list[dict[str, Any]] = []

    def run_monthly(self, callback: Callable[..., Any], **kwargs: Any) -> None:
        self.monthly.append({"callback": callback, **kwargs})

    def run_once(self, callback: Callable[..., Any], **kwargs: Any) -> None:
        self.once.append({"callback": callback, **kwargs})


def test_schedule_builds_on_the_first_at_family_hour(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(clock.TZ_ENV, "Asia/Tokyo")
    queue = FakeJobQueue()
    dj.schedule(queue, hour=9)  # type: ignore[arg-type]
    (job,) = queue.monthly
    assert job["callback"] is dj.run_build
    assert job["day"] == 1 and job["name"] == dj.JOB_NAME
    assert job["when"].hour == 9 and str(job["when"].tzinfo) == "Asia/Tokyo"


def _with_sheets(monkeypatch: pytest.MonkeyPatch) -> None:
    real_load = config.load
    sheets = FakeSheetsClient(categories=["Еда"])
    monkeypatch.setattr(config, "load", lambda env: real_load(env, open_sheets=lambda c, n: sheets))
    monkeypatch.setattr(config, "_client_email", lambda creds, problems: "bot@example.test")
    monkeypatch.setenv("GOOGLE_CREDS_PATH", "creds.json")
    monkeypatch.setenv("SHEET_NAME", "Family")
    monkeypatch.setenv("ALLOWED_USERS", "111:Ann")


def _app(queue: FakeJobQueue) -> SimpleNamespace:
    class Bot:
        async def set_my_commands(self, commands: list[Any]) -> bool:
            return True

    return SimpleNamespace(bot_data={}, bot=Bot(), job_queue=queue)


def test_post_init_schedules_build_at_summary_hour(monkeypatch: pytest.MonkeyPatch) -> None:
    _with_sheets(monkeypatch)
    monkeypatch.setenv(config.SUMMARY_HOUR_ENV, "8")
    queue = FakeJobQueue()
    run(bot.post_init(_app(queue)))  # type: ignore[arg-type]
    (job,) = [j for j in queue.monthly if j["callback"] is dj.run_build]
    assert job["when"].hour == 8


def test_post_init_without_chat_has_no_build_job(monkeypatch: pytest.MonkeyPatch) -> None:
    _with_sheets(monkeypatch)
    monkeypatch.delenv("DASHBOARD_CHAT_ID")
    queue = FakeJobQueue()
    run(bot.post_init(_app(queue)))  # type: ignore[arg-type]
    assert not [j for j in queue.monthly if j["callback"] is dj.run_build]


def test_job_runs_build_off_the_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    threads: list[bool] = []

    def build() -> bool:
        try:
            asyncio.get_running_loop()
            threads.append(False)
        except RuntimeError:
            threads.append(True)  # no loop here: we are in a worker thread
        return True

    monkeypatch.setattr(bd, "build_from_env", build)
    run(dj.run_build(SimpleNamespace(bot_data={})))  # type: ignore[arg-type]
    assert threads == [True]


def test_second_job_while_building_is_skipped(
        monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    started: list[int] = []

    def slow_build() -> bool:
        started.append(1)
        return True

    monkeypatch.setattr(bd, "build_from_env", slow_build)
    context = SimpleNamespace(bot_data={})

    async def both() -> None:
        await asyncio.gather(dj.run_build(context), dj.run_build(context))  # type: ignore[arg-type]

    run(both())
    assert started == [1]


# ---------- /build_dashboard ----------

def _command_context(**fields: Any) -> tuple[SimpleNamespace, FakeJobQueue]:
    queue = FakeJobQueue()
    ctx = bot_context(**fields)
    ctx.job_queue = queue
    return ctx, queue


def test_command_starts_build_now() -> None:
    ctx, queue = _command_context(summary_chat_id=4242, sheets=FakeSheetsClient())
    msg = FakeMessage("/build_dashboard")
    run(dj.build_now(SimpleNamespace(effective_message=msg), ctx))  # type: ignore[arg-type]
    (job,) = queue.once
    assert job["callback"] is dj.run_build and job["when"] == 0
    assert msg.replies[-1][0] == dj.STARTED


def test_command_while_building_says_busy() -> None:
    ctx, queue = _command_context(summary_chat_id=4242, sheets=FakeSheetsClient())
    msg = FakeMessage("/build_dashboard")

    async def press_during_build() -> None:
        async with dj.build_lock(ctx.bot_data):
            await dj.build_now(SimpleNamespace(effective_message=msg), ctx)  # type: ignore[arg-type]

    run(press_during_build())
    assert queue.once == []
    assert msg.replies[-1][0] == dj.BUSY


@pytest.mark.parametrize("fields", [
    {"summary_chat_id": None, "sheets": FakeSheetsClient()},
    {"summary_chat_id": 4242, "sheets": None},
])
def test_command_without_chat_or_sheets_explains(fields: dict[str, Any]) -> None:
    ctx, queue = _command_context(**fields)
    msg = FakeMessage("/build_dashboard")
    run(dj.build_now(SimpleNamespace(effective_message=msg), ctx))  # type: ignore[arg-type]
    assert queue.once == []
    assert msg.replies[-1][0] == dj.NOT_SET


def test_command_is_in_the_menu() -> None:
    assert "build_dashboard" in [c.command for c in bot.COMMANDS]


def test_no_dashboard_yet_points_at_the_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot, "get_dashboard_file_id", lambda sheets: None)
    msg = FakeMessage("/dashboard")
    run(bot.send_dashboard(SimpleNamespace(effective_message=msg), bot_context()))
    text = msg.replies[-1][0]
    assert "1-го числа" in text and "/build_dashboard" in text
    assert "run.sh" not in text
