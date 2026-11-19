"""Logging setup at entry points: level from LOG_LEVEL, secrets masked (C7)."""
from __future__ import annotations

import io
import logging

import pytest

from family_bot import logs

TOKEN = "123456789:AAH-fake-token-value-for-tests_abcdef"


def setup_to_buffer(**env: str) -> io.StringIO:
    stream = io.StringIO()
    logs.setup(env, stream=stream)
    return stream


def test_default_level_is_info() -> None:
    out = setup_to_buffer()
    log = logging.getLogger("family_bot.test")
    log.debug("hidden detail")
    log.info("visible line")
    assert logging.getLogger().level == logging.INFO
    assert "visible line" in out.getvalue()
    assert "hidden detail" not in out.getvalue()


def test_level_comes_from_env() -> None:
    out = setup_to_buffer(LOG_LEVEL="debug")
    logging.getLogger("family_bot.test").debug("detail shown")
    assert logging.getLogger().level == logging.DEBUG
    assert "detail shown" in out.getvalue()


def test_unknown_level_falls_back_to_info_with_one_line() -> None:
    out = setup_to_buffer(LOG_LEVEL="loud")
    assert logging.getLogger().level == logging.INFO
    lines = [line for line in out.getvalue().splitlines() if "LOG_LEVEL" in line]
    assert len(lines) == 1 and "loud" in lines[0]


def test_one_record_is_one_line_with_level_and_module() -> None:
    out = setup_to_buffer()
    logging.getLogger("family_bot.bot").warning("⚠️ SHEET_NAME не задан — впишите его в .env")
    lines = out.getvalue().splitlines()
    assert len(lines) == 1
    assert "WARNING family_bot.bot: ⚠️ SHEET_NAME" in lines[0]


def test_setup_twice_keeps_one_handler() -> None:
    out = setup_to_buffer()
    logs.setup({}, stream=out)
    logging.getLogger("family_bot.test").info("once")
    assert out.getvalue().count("once") == 1


def test_token_never_reaches_log_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", TOKEN)
    out = setup_to_buffer(LOG_LEVEL="DEBUG")
    log = logging.getLogger("family_bot.test")
    log.info("POST https://api.telegram.org/bot%s/sendMessage", TOKEN)
    try:
        raise OSError(f"cannot reach https://api.telegram.org/bot{TOKEN}/getMe")
    except OSError:
        log.exception("Request failed")
    text = out.getvalue()
    assert TOKEN not in text
    assert "Request failed" in text and "OSError" in text


def test_token_shaped_string_is_masked_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    out = setup_to_buffer()
    logging.getLogger("family_bot.test").info("stale %s", TOKEN)
    assert TOKEN not in out.getvalue()


def test_http_client_request_lines_are_quiet() -> None:
    out = setup_to_buffer()
    # httpx logs every Telegram request URL (with the token) at INFO.
    logging.getLogger("httpx").info("HTTP Request: POST https://api.telegram.org/bot%s", TOKEN)
    assert out.getvalue() == ""
