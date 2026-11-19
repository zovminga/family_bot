"""Bot-wide error handling: user gets a short reply, details go to a masked log."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from telegram import Chat, Message, Update, User
from telegram.ext import Application, ExtBot, MessageHandler, filters

import bot
import errors
from tests.conftest import bot_context
from tests.known_bugs.fakes import FakeMessage, FakeQuery, make_update

BOT_TOKEN = "123456789:AAH-secretTokenValue_abcdefghijklmnop"
API_KEY = "sk-ant-api03-secretKeyValue"


def run(coro: Any) -> Any:
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture(autouse=True)
def _secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setenv("ANTHROPIC_API_KEY", API_KEY)


# ---------- mask_secrets ----------

def test_mask_secrets_hides_env_token_and_key() -> None:
    text = f"POST https://api.telegram.org/bot{BOT_TOKEN}/sendMessage key={API_KEY}"
    masked = errors.mask_secrets(text)
    assert BOT_TOKEN not in masked
    assert API_KEY not in masked
    assert "api.telegram.org/bot" in masked


def test_mask_secrets_hides_token_shapes_not_in_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BOT_TOKEN")
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    other_token = "987654321:BBx_otherTokenValue-0123456789abcdefg"
    other_key = "sk-ant-api03-otherKey_value-123"
    masked = errors.mask_secrets(f"{other_token} {other_key}")
    assert other_token not in masked
    assert other_key not in masked


# ---------- on_error ----------

def _context(error: BaseException) -> SimpleNamespace:
    return SimpleNamespace(error=error)


def test_on_error_replies_to_user_and_logs_masked(caplog: pytest.LogCaptureFixture) -> None:
    upd = make_update("5000")
    caplog.set_level(logging.ERROR, logger=errors.logger.name)
    run(errors.on_error(upd, _context(RuntimeError(f"bad url .../bot{BOT_TOKEN}/x"))))
    assert [t for t, _ in upd.message.replies] == [errors.SAVE_FAILED]
    log = caplog.text
    assert "RuntimeError" in log
    assert BOT_TOKEN not in log


def test_on_error_on_callback_replies_in_card_chat() -> None:
    query = FakeQuery("vx:ab12:save")
    upd = make_update(query=query)
    run(errors.on_error(upd, _context(RuntimeError("APIError: [429] Quota exceeded"))))
    assert [t for t, _ in query.message.replies] == [errors.SAVE_FAILED]


def test_on_error_without_update_only_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.ERROR, logger=errors.logger.name)
    run(errors.on_error(None, _context(RuntimeError("network down"))))
    assert "network down" in caplog.text


def test_on_error_survives_failed_reply(caplog: pytest.LogCaptureFixture) -> None:
    class DeadMessage(FakeMessage):
        async def reply_text(self, text: str, **kw: Any) -> FakeMessage:
            raise RuntimeError("telegram unreachable")

    upd = SimpleNamespace(effective_message=DeadMessage())
    caplog.set_level(logging.ERROR, logger=errors.logger.name)
    run(errors.on_error(upd, _context(RuntimeError("first"))))
    assert "telegram unreachable" in caplog.text


# ---------- wiring: a raising handler leads to a reply (R3) ----------

def test_bot_registers_error_handler() -> None:
    app = bot.build_application(BOT_TOKEN)
    assert errors.on_error in app.error_handlers


def test_raising_handler_leads_to_user_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[str] = []

    async def fake_reply_text(self: Message, text: str, **kw: Any) -> None:
        sent.append(text)

    async def no_network(self: ExtBot) -> None:
        return None

    monkeypatch.setattr(Message, "reply_text", fake_reply_text)
    monkeypatch.setattr(ExtBot, "initialize", no_network)
    monkeypatch.setattr(ExtBot, "shutdown", no_network)

    async def boom(update: Update, context: Any) -> None:
        raise RuntimeError("Sheets is down")

    async def scenario() -> None:
        app = Application.builder().token(BOT_TOKEN).updater(None).build()
        app.add_handler(MessageHandler(filters.TEXT, boom))
        app.add_error_handler(errors.on_error)
        await app.initialize()
        msg = Message(1, datetime.now(timezone.utc), Chat(1, "private"),
                      from_user=User(1, "A", False), text="5000")
        await app.process_update(Update(1, message=msg))
        await app.shutdown()

    run(scenario())
    assert sent == [errors.SAVE_FAILED]


# ---------- voice / photo: no raw exception text to the user (U5) ----------

class _VoiceMsg(FakeMessage):
    def __init__(self) -> None:
        super().__init__()
        self.voice = SimpleNamespace(file_id="f1")
        self.audio = None
        self.photo = [SimpleNamespace(file_id="p1")]
        self.document = None
        self.status = FakeMessage()

    async def reply_text(self, text: str, **kw: Any) -> FakeMessage:
        self.replies.append((text, kw))
        return self.status


class _FakeFile:
    async def download_as_bytearray(self) -> bytearray:
        return bytearray(b"data")


class _FakeBot:
    async def get_file(self, file_id: str) -> _FakeFile:
        return _FakeFile()


RAW = "Error code: 529 - {'type': 'overloaded_error'}"


def _media_update() -> tuple[SimpleNamespace, _VoiceMsg]:
    msg = _VoiceMsg()
    upd = SimpleNamespace(
        message=msg, effective_message=msg, callback_query=None,
        effective_user=SimpleNamespace(id=111, username="u", first_name="U"))
    return upd, msg


def _ctx(bot: Any = None) -> SimpleNamespace:
    ctx = bot_context()
    ctx.bot = bot or _FakeBot()
    return ctx


def _raise(*a: Any, **kw: Any) -> Any:
    raise RuntimeError(RAW)


def test_voice_claude_error_shows_not_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot.vx, "transcribe", lambda b: "чипсы сто динар")
    monkeypatch.setattr(bot.vx, "extract_expenses", _raise)
    upd, msg = _media_update()
    run(bot.voice_handler(upd, _ctx()))
    final = msg.status.edits[-1][0]
    assert errors.NOT_PARSED in final
    assert "529" not in final


def test_voice_stt_error_shows_not_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot.vx, "transcribe", _raise)
    upd, msg = _media_update()
    run(bot.voice_handler(upd, _ctx()))
    final = msg.status.edits[-1][0]
    assert errors.NOT_PARSED in final
    assert "529" not in final


def test_photo_claude_error_shows_not_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot.px, "extract_receipt", _raise)
    upd, msg = _media_update()
    run(bot.photo_handler(upd, _ctx()))
    final = msg.status.edits[-1][0]
    assert errors.NOT_PARSED in final
    assert "529" not in final


def test_voice_download_error_hides_raw_text() -> None:
    class BrokenBot:
        async def get_file(self, file_id: str) -> Any:
            raise RuntimeError(RAW)

    upd, msg = _media_update()
    run(bot.voice_handler(upd, _ctx(BrokenBot())))
    final = msg.status.edits[-1][0]
    assert "529" not in final and final.startswith("❌")


def test_dashboard_send_error_hides_raw_text(monkeypatch: pytest.MonkeyPatch) -> None:
    class Msg(FakeMessage):
        async def reply_document(self, **kw: Any) -> None:
            raise RuntimeError(RAW)

    monkeypatch.setattr(bot, "get_dashboard_file_id", lambda sheets: "file1")
    msg = Msg()
    run(bot.send_dashboard(SimpleNamespace(effective_message=msg), _ctx()))
    text = msg.replies[-1][0]
    assert "529" not in text and text.startswith("❌")


def test_test_connection_error_hides_raw_text() -> None:
    upd = make_update("/test_connection")
    run(bot.test_connection(upd, bot_context(sheets=SimpleNamespace(test_connection=_raise))))
    text = upd.message.replies[-1][0]
    assert "529" not in text and text.startswith("❌")
