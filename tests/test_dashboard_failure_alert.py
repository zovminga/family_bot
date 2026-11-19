"""Failed dashboard build sends a short Telegram alert (finding D5).

The sender is replaced with a fake that records calls, so no test reaches the
network. The .env fallback path points at a temp file, so a real .env in the
checkout is never read.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

DASHBOARD_DIR = Path(__file__).resolve().parents[1] / "dashboard"
if str(DASHBOARD_DIR) not in sys.path:
    sys.path.insert(0, str(DASHBOARD_DIR))

import build_dashboard as bd  # noqa: E402
import failure_alert as fa  # noqa: E402

TOKEN = "123456:SECRET-token-value"
CHAT_ID = "4242"


class FakeSender:
    """Records (token, chat_id, text) for every send; can raise instead."""

    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.error = error

    def __call__(self, token: str, chat_id: str, text: str) -> None:
        self.calls.append((token, chat_id, text))
        if self.error is not None:
            raise self.error


@pytest.fixture(autouse=True)
def no_real_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(fa, "ENV_PATH", tmp_path / ".env")
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.delenv("DASHBOARD_CHAT_ID", raising=False)


@pytest.fixture
def creds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", TOKEN)
    monkeypatch.setenv("DASHBOARD_CHAT_ID", CHAT_ID)


def test_alert_text_names_reason_and_log() -> None:
    text = fa.failure_text("сборка: RuntimeError", TOKEN)
    assert "Сборка дашборда не удалась" in text
    assert "сборка: RuntimeError" in text
    assert "launchd.log" in text


def test_alert_text_masks_token_and_stays_short() -> None:
    text = fa.failure_text(f"bad url bot{TOKEN}/x " + "x" * 2000, TOKEN)
    assert TOKEN not in text
    assert "<BOT_TOKEN>" in text
    assert len(text) < 500


def test_send_uses_dashboard_chat(creds: None) -> None:
    sender = FakeSender()
    assert fa.send_failure_alert("сборка: RuntimeError", send=sender) is True
    [(token, chat_id, text)] = sender.calls
    assert (token, chat_id) == (TOKEN, CHAT_ID)
    assert "сборка: RuntimeError" in text


def test_send_reads_env_file_when_env_is_empty(tmp_path: Path) -> None:
    fa.ENV_PATH.write_text(
        f"# comment\nBOT_TOKEN='{TOKEN}'\nDASHBOARD_CHAT_ID=\"{CHAT_ID}\"\n", encoding="utf-8")
    sender = FakeSender()
    assert fa.send_failure_alert("x", send=sender) is True
    assert sender.calls[0][:2] == (TOKEN, CHAT_ID)


def test_send_skips_without_credentials(capsys: pytest.CaptureFixture[str]) -> None:
    sender = FakeSender()
    assert fa.send_failure_alert("x", send=sender) is False
    assert sender.calls == []
    assert "not set" in capsys.readouterr().err


def test_send_error_is_reported_masked(creds: None, capsys: pytest.CaptureFixture[str]) -> None:
    sender = FakeSender(OSError(f"cannot reach https://api.telegram.org/bot{TOKEN}/sendMessage"))
    assert fa.send_failure_alert("x", send=sender) is False
    err = capsys.readouterr().err
    assert TOKEN not in err
    assert "<BOT_TOKEN>" in err


def test_cli_sends_reason(creds: None, monkeypatch: pytest.MonkeyPatch) -> None:
    sender = FakeSender()
    monkeypatch.setattr(fa, "post_message", sender)
    assert fa.main(["создание venv (код 1)"]) == 0
    assert "создание venv (код 1)" in sender.calls[0][2]


def _run_main(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> int:
    monkeypatch.setattr(sys, "argv", ["build_dashboard.py", *argv])
    with pytest.raises(SystemExit) as exc_info:
        bd.main()
    code = exc_info.value.code
    assert isinstance(code, int)
    return code


def test_failed_publish_build_alerts_without_error_text(
        creds: None, monkeypatch: pytest.MonkeyPatch) -> None:
    # Build errors can quote sheet rows (spender, amount, comment). Only the
    # stage and the exception type may leave the machine.
    def broken_build(**_kwargs: object) -> Path:
        raise RuntimeError("1 row(s) have a Currency with no FX rate: Lisa 1500 аренда")

    sender = FakeSender()
    monkeypatch.setattr(fa, "post_message", sender)
    monkeypatch.setattr(bd, "build", broken_build)

    assert _run_main(monkeypatch, ["--publish"]) == fa.ALERT_HANDLED_EXIT_CODE
    [(_, _, text)] = sender.calls
    assert "сборка: RuntimeError" in text
    assert "Lisa" not in text and "1500" not in text and "аренда" not in text


def test_failed_publish_step_names_publish_stage(
        creds: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def broken_publish(_path: Path) -> None:
        raise ConnectionError("telegram down")

    sender = FakeSender()
    monkeypatch.setattr(fa, "post_message", sender)
    monkeypatch.setattr(bd, "build", lambda **_kwargs: tmp_path / "out.html")
    monkeypatch.setattr(bd, "publish_to_telegram", broken_publish)

    assert _run_main(monkeypatch, ["--publish"]) == fa.ALERT_HANDLED_EXIT_CODE
    assert "публикация в Telegram: ConnectionError" in sender.calls[0][2]


def test_failed_build_without_publish_does_not_alert(
        creds: None, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken_build(**_kwargs: object) -> Path:
        raise RuntimeError("boom")

    sender = FakeSender()
    monkeypatch.setattr(fa, "post_message", sender)
    monkeypatch.setattr(bd, "build", broken_build)
    monkeypatch.setattr(sys, "argv", ["build_dashboard.py"])

    with pytest.raises(RuntimeError, match="boom"):
        bd.main()
    assert sender.calls == []
