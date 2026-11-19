"""Bot startup without Google Sheets: /whoami works, other input says what to set up (I3, I4)."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from telegram.ext import ApplicationHandlerStop

from family_bot import bot
from family_bot import config
from tests.conftest import FakeSheetsClient, bot_context, settings_of
from tests.known_bugs.fakes import make_update

NO_SHEET = config.problem("SHEET_NAME is not set", "set it in .env")


def run(coro: Any) -> Any:
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def replies(update: Any) -> list[str]:
    return [t for t, _ in update.message.replies]


def test_whoami_answers_without_google() -> None:
    ctx = bot_context(problems=[NO_SHEET])
    upd = make_update("/whoami", user_id=111)
    run(bot.enforce_allowlist(upd, ctx))  # does not stop the update
    run(bot.whoami(upd, ctx))
    assert "ID: 111" in replies(upd)[0]
    assert "Allowed as: Azat" in replies(upd)[0]


def test_stranger_whoami_without_google_gives_id() -> None:
    ctx = bot_context(problems=[NO_SHEET])
    upd = make_update("/whoami", user_id=999)
    run(bot.enforce_allowlist(upd, ctx))
    run(bot.whoami(upd, ctx))
    assert "ALLOWED_USERS=999:" in replies(upd)[0]


def test_other_input_without_google_explains_setup() -> None:
    ctx = bot_context(problems=[NO_SHEET])
    upd = make_update("💰 Add expense", user_id=111)
    with pytest.raises(ApplicationHandlerStop):
        run(bot.enforce_allowlist(upd, ctx))
    text = replies(upd)[0]
    assert NO_SHEET in text
    assert "/whoami" in text


def test_stranger_is_denied_before_setup_hint() -> None:
    ctx = bot_context(problems=[NO_SHEET])
    upd = make_update("💰 Add expense", user_id=999)
    with pytest.raises(ApplicationHandlerStop):
        run(bot.enforce_allowlist(upd, ctx))
    assert NO_SHEET not in replies(upd)[0]
    assert "999" in replies(upd)[0]


def test_allowed_user_with_sheets_passes() -> None:
    ctx = bot_context(sheets=FakeSheetsClient())
    run(bot.enforce_allowlist(make_update("💰 Add expense", user_id=111), ctx))


def test_post_init_puts_config_in_bot_data(monkeypatch: pytest.MonkeyPatch,
                                           capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("ALLOWED_USERS", "111:Иван, Петров")
    app = SimpleNamespace(bot_data={})
    run(bot.post_init(app))  # type: ignore[arg-type]
    settings = settings_of(app)
    assert settings.sheets is None
    assert settings.allowed_users == {111: "Иван, Петров"}
    out = capsys.readouterr().out
    assert "Traceback" not in out
    assert any(line.startswith("⚠️ GOOGLE_CREDS_PATH") and " — " in line
               for line in out.splitlines()), out


def test_reloadcats_rereads_categories() -> None:
    fake = FakeSheetsClient(categories=["Food"])
    ctx = bot_context(sheets=fake, categories=["Old"])
    upd = make_update("/reloadcats")
    run(bot.reload_cats(upd, ctx))
    assert settings_of(ctx).categories == ["Food"]
    assert replies(upd)[0].startswith("🔄")


def test_reloadcats_shows_problems_to_the_user() -> None:
    ctx = bot_context(sheets=FakeSheetsClient(categories=[]))
    upd = make_update("/reloadcats")
    run(bot.reload_cats(upd, ctx))
    text = replies(upd)[0]
    assert "Config sheet has no categories" in text


def test_main_without_bot_token_is_one_line(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.setattr(bot.config, "load_env_file", lambda: None)
    with pytest.raises(SystemExit) as exc:
        bot.main()
    message = str(exc.value)
    assert "BOT_TOKEN" in message and " — " in message and "\n" not in message


def test_main_registers_post_init(monkeypatch: pytest.MonkeyPatch) -> None:
    apps: list[Any] = []
    monkeypatch.setenv("BOT_TOKEN", "123:abc")
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    monkeypatch.setattr(bot.config, "load_env_file", lambda: None)
    monkeypatch.setattr(bot.Application, "run_polling", lambda self, **kw: apps.append(self))
    bot.main()
    assert apps[0].post_init is bot.post_init


def run_main_in_webhook_mode(monkeypatch: pytest.MonkeyPatch,
                             secret: str | None) -> dict[str, Any]:
    """Start main() with RENDER_EXTERNAL_URL set and return run_webhook's kwargs."""
    calls: list[dict[str, Any]] = []
    monkeypatch.setenv("BOT_TOKEN", "123:abc")
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://bot.example.com")
    if secret is None:
        monkeypatch.delenv(config.WEBHOOK_SECRET_ENV, raising=False)
    else:
        monkeypatch.setenv(config.WEBHOOK_SECRET_ENV, secret)
    monkeypatch.setattr(bot.config, "load_env_file", lambda: None)
    monkeypatch.setattr(bot.Application, "run_webhook", lambda self, **kw: calls.append(kw))
    bot.main()
    return calls[0]


def test_webhook_url_has_no_bot_token(monkeypatch: pytest.MonkeyPatch) -> None:
    kw = run_main_in_webhook_mode(monkeypatch, "s3cret_value-1")
    assert "123:abc" not in kw["url_path"]
    assert "123:abc" not in kw["webhook_url"]
    assert kw["webhook_url"] == f"https://bot.example.com/{kw['url_path']}"


def test_webhook_passes_secret_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    kw = run_main_in_webhook_mode(monkeypatch, "s3cret_value-1")
    assert kw["secret_token"] == "s3cret_value-1"


def test_webhook_without_secret_env_generates_one(monkeypatch: pytest.MonkeyPatch,
                                                  capsys: pytest.CaptureFixture[str]) -> None:
    kw = run_main_in_webhook_mode(monkeypatch, None)
    assert config.is_valid_webhook_secret(kw["secret_token"])
    assert len(kw["secret_token"]) >= 32
    assert config.WEBHOOK_SECRET_ENV in capsys.readouterr().out


def test_webhook_with_bad_secret_is_one_line(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(SystemExit) as exc:
        run_main_in_webhook_mode(monkeypatch, "has spaces!")
    message = str(exc.value)
    assert config.WEBHOOK_SECRET_ENV in message and " — " in message and "\n" not in message
    assert "has spaces!" not in message  # the value is a secret, do not echo it
