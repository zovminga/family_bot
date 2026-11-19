"""scripts/test_google_sheets.py reads .env before checking the connection (I5)."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import dotenv.main
import pytest

from tests.conftest import ROOT, FakeSheetsClient


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "check_sheets", ROOT / "scripts" / "test_google_sheets.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_google_check_reads_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                     capsys: pytest.CaptureFixture[str]) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("SHEET_NAME=FromDotEnv\n")
    monkeypatch.setenv("SHEET_NAME", "")
    monkeypatch.delenv("SHEET_NAME")  # teardown removes what the script loads
    script = load_script()
    monkeypatch.setattr(script, "ENV_FILE", env_file)
    # conftest stubs dotenv.load_dotenv, dotenv.main keeps the real one
    monkeypatch.setattr(script, "load_dotenv", dotenv.main.load_dotenv)
    monkeypatch.setattr(script, "open_client", lambda: FakeSheetsClient(categories=["Food"]))
    script.main()
    out = capsys.readouterr().out
    assert "SHEET_NAME: FromDotEnv" in out
