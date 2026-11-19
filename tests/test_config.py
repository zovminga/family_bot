"""Config: settings loaded at startup, one line per setup problem (C2, I3, I4, I7)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import gspread
import pytest

import config
from tests.conftest import ROOT, FakeSheetsClient

REGULAR = [
    ["название", "динары", "евро", "категория", "кто"],
    ["Rent", "", "850", "Rent", "Both"],
]


@pytest.fixture
def key_file(tmp_path: Path) -> Path:
    path = tmp_path / "creds.json"
    path.write_text(json.dumps({"client_email": "bot@x.iam.gserviceaccount.com",
                                "private_key": "-----BEGIN PRIVATE KEY-----"}))
    return path


def env(key_file: Path | None = None, **extra: str) -> dict[str, str]:
    values = {"ALLOWED_USERS": "111:Azat"}
    if key_file is not None:
        values.update(GOOGLE_CREDS_PATH=str(key_file), SHEET_NAME="Family")
    values.update(extra)
    return values


def opener(client: Any) -> Any:
    def open_sheets(creds_path: str, sheet_name: str) -> Any:
        if isinstance(client, Exception):
            raise client
        return client
    return open_sheets


def assert_one_line_each(problems: list[str]) -> None:
    assert problems
    for line in problems:
        assert "\n" not in line and "Traceback" not in line, line
        assert " — " in line, f"no fix given: {line}"


# ---------- import ----------

def test_import_reads_no_env_and_opens_no_sheet() -> None:
    probe = (
        "import dotenv, sheets\n"
        "def boom(*a, **k): raise SystemExit('touched ' + str(a))\n"
        "dotenv.load_dotenv = boom\n"
        "sheets.open_client = boom\n"
        "sheets._authorize_spreadsheet = boom\n"
        "import bot, config\n"
        "print('ok')\n"
    )
    result = subprocess.run([sys.executable, "-c", probe], cwd=ROOT,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


# ---------- happy path ----------

def test_load_reads_categories_presets_allowlist(key_file: Path) -> None:
    fake = FakeSheetsClient(categories=[" Food", "Transport", "Food", ""],
                            regular_values=REGULAR)
    cfg = config.load(env(key_file), open_sheets=opener(fake))
    assert cfg.problems == []
    assert cfg.sheets is fake
    assert cfg.categories == ["Food", "Transport"]
    assert [p["name"] for p in cfg.presets] == ["Rent"]
    assert cfg.allowed_users == {111: "Azat"}
    assert "$" in cfg.currencies


def test_reload_refreshes_categories_and_presets(key_file: Path) -> None:
    fake = FakeSheetsClient(categories=["Food"])
    cfg = config.load(env(key_file), open_sheets=opener(fake))
    fake._categories = ["Food", "Rent"]
    fake._regular_values = REGULAR
    config.reload(cfg)
    assert cfg.categories == ["Food", "Rent"]
    assert [p["name"] for p in cfg.presets] == ["Rent"]


# ---------- Google not configured: bot still starts ----------

def test_no_google_settings_gives_one_line_per_problem() -> None:
    def never(*a: Any) -> Any:
        raise AssertionError("must not open Sheets without settings")

    cfg = config.load(env(), open_sheets=never)
    assert cfg.sheets is None
    assert cfg.allowed_users == {111: "Azat"}
    assert_one_line_each(cfg.problems)
    text = " ".join(cfg.problems)
    assert "GOOGLE_CREDS_PATH" in text and "SHEET_NAME" in text


def test_missing_key_file_is_one_line(tmp_path: Path) -> None:
    cfg = config.load(env(tmp_path / "nope.json"), open_sheets=opener(FakeSheetsClient()))
    assert cfg.sheets is None
    assert_one_line_each(cfg.problems)
    assert "nope.json" in cfg.problems[0]


def test_key_file_that_is_not_a_service_account_key(tmp_path: Path) -> None:
    bad = tmp_path / "creds.json"
    bad.write_text("not json")
    cfg = config.load(env(bad), open_sheets=opener(FakeSheetsClient()))
    assert cfg.sheets is None
    assert_one_line_each(cfg.problems)
    assert "client_email" in cfg.problems[0]


def test_spreadsheet_not_shared_names_client_email(key_file: Path) -> None:
    cfg = config.load(env(key_file),
                      open_sheets=opener(gspread.SpreadsheetNotFound("404")))
    assert cfg.sheets is None
    assert_one_line_each(cfg.problems)
    assert "Family" in cfg.problems[0]
    assert "bot@x.iam.gserviceaccount.com" in cfg.problems[0]


def test_other_open_error_is_one_masked_line(key_file: Path) -> None:
    token = "123456789:AAH-secretTokenValue_abcdefghijklmnop"
    err = ValueError(f"('Could not deserialize key data.', {token})\nsecond line")
    cfg = config.load(env(key_file), open_sheets=opener(err))
    assert cfg.sheets is None
    assert_one_line_each(cfg.problems)
    assert "ValueError" in cfg.problems[0]
    assert token not in cfg.problems[0]


def test_empty_config_sheet_falls_back_with_a_problem(key_file: Path) -> None:
    cfg = config.load(env(key_file), open_sheets=opener(FakeSheetsClient()))
    assert cfg.sheets is not None
    assert cfg.categories == config.FALLBACK_CATEGORIES
    assert_one_line_each(cfg.problems)
    assert "Config" in cfg.problems[0]


# ---------- ALLOWED_USERS ----------

def test_comma_inside_name_does_not_crash(key_file: Path) -> None:
    cfg = config.load(env(key_file, ALLOWED_USERS="111:Иван, Петров,222:Lisa"),
                      open_sheets=opener(FakeSheetsClient(categories=["Food"])))
    assert cfg.allowed_users == {111: "Иван, Петров", 222: "Lisa"}
    assert cfg.problems == []


def test_bad_allowlist_is_a_problem_not_a_crash(key_file: Path) -> None:
    cfg = config.load(env(key_file, ALLOWED_USERS="Azat:111"),
                      open_sheets=opener(FakeSheetsClient(categories=["Food"])))
    assert cfg.allowed_users == {}
    assert_one_line_each(cfg.problems)
    assert "ALLOWED_USERS" in cfg.problems[0]


def test_empty_allowlist_is_a_problem(key_file: Path) -> None:
    cfg = config.load(env(key_file, ALLOWED_USERS=""),
                      open_sheets=opener(FakeSheetsClient(categories=["Food"])))
    assert_one_line_each(cfg.problems)
    assert "/whoami" in cfg.problems[0]
