#!/usr/bin/env python3
"""Bot settings, loaded once at startup (``post_init``) and kept in ``bot_data``.

Importing this module (or ``bot``) reads no ``.env`` and opens no spreadsheet.
``load`` never raises on a setup mistake: each problem becomes one line
«what is wrong — what to do» in ``Config.problems``, and the bot still starts
so ``/whoami`` works before Google Sheets is set up.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Optional

import gspread
from dotenv import load_dotenv

import auth
import currency
import errors
import regular
from sheets import SheetsClient, open_client, resolve_creds_path

KEY = "config"  # bot_data key
ENV_FILE = Path(__file__).resolve().parent / ".env"
# Used when the Config sheet is empty or unreadable, so the add-expense flow still has buttons.
FALLBACK_CATEGORIES = ["Food", "Transport", "Entertainment", "Other"]

OpenSheets = Callable[[str, str], SheetsClient]


@dataclass
class Config:
    """Everything handlers read that comes from settings or the spreadsheet."""

    allowed_users: dict[int, str] = field(default_factory=dict)
    sheets: Optional[SheetsClient] = None  # None: Google Sheets is not set up
    categories: list[str] = field(default_factory=list)
    presets: list[dict] = field(default_factory=list)
    currencies: list[str] = field(default_factory=currency.symbols)
    problems: list[str] = field(default_factory=list)


def problem(what: str, fix: str) -> str:
    """One setup problem as a single line: what is wrong — what to do."""
    return f"⚠️ {what} — {fix}"


def load_env_file(path: Path = ENV_FILE) -> None:
    """Read ``.env`` into the environment (existing variables win)."""
    load_dotenv(path)


def _clean_categories(raw: list[str]) -> list[str]:
    """Strip names, drop blanks and repeats, keep sheet order."""
    seen: list[str] = []
    for name in raw:
        name = name.strip()
        if name and name not in seen:
            seen.append(name)
    return seen


def _first_line(exc: BaseException) -> str:
    text = errors.mask_secrets(str(exc)).strip().splitlines()
    return f"{type(exc).__name__}: {text[0]}" if text else type(exc).__name__


def _allowed_users(raw: Optional[str], problems: list[str]) -> dict[int, str]:
    try:
        users = auth.parse_allowed_users(raw)
    except ValueError as e:
        problems.append(problem(
            f"ALLOWED_USERS is not valid ({_first_line(e)})",
            "write it as 111111111:Имя,222222222:Имя in .env and restart"))
        return {}
    if not users:
        problems.append(problem(
            "ALLOWED_USERS is empty, nobody can use the bot",
            "send /whoami to the bot and add the ID to ALLOWED_USERS in .env, then restart"))
    return users


def _client_email(creds: Path, problems: list[str]) -> Optional[str]:
    """client_email of a service account key file, or None after adding a problem."""
    if not creds.exists():
        problems.append(problem(
            f"GOOGLE_CREDS_PATH: file {creds} not found",
            "download the service account JSON key (README → Google Sheets) and fix the path"))
        return None
    try:
        key = json.loads(creds.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        key = None
    if not isinstance(key, dict) or not key.get("client_email") or not key.get("private_key"):
        problems.append(problem(
            f"GOOGLE_CREDS_PATH: {creds} is not a service account key "
            "(no client_email / private_key)",
            "download a new JSON key on the service account's Keys tab"))
        return None
    return str(key["client_email"])


def _open_sheets(env: Mapping[str, str], open_sheets: OpenSheets,
                 problems: list[str]) -> Optional[SheetsClient]:
    creds_raw, sheet_name = env.get("GOOGLE_CREDS_PATH"), env.get("SHEET_NAME")
    if not creds_raw:
        problems.append(problem(
            "GOOGLE_CREDS_PATH is not set",
            "set it in .env to the service account JSON key, e.g. secrets/creds.json"))
    if not sheet_name:
        problems.append(problem(
            "SHEET_NAME is not set",
            "set it in .env to the exact title of the Google Spreadsheet"))
    if not creds_raw or not sheet_name:
        return None
    creds = resolve_creds_path(creds_raw)
    email = _client_email(creds, problems)
    if email is None:
        return None
    try:
        return open_sheets(str(creds), sheet_name)
    except gspread.SpreadsheetNotFound:
        problems.append(problem(
            f"spreadsheet {sheet_name!r} not found",
            f"check SHEET_NAME and share the spreadsheet with {email} as Editor"))
    except Exception as e:  # noqa: BLE001 - any open failure is a setup problem
        problems.append(problem(
            f"cannot open Google Sheets ({_first_line(e)})",
            "check the key file, SHEET_NAME and network, then run "
            "python scripts/test_google_sheets.py"))
    return None


def reload(cfg: Config) -> list[str]:
    """Re-read categories and presets from the spreadsheet into ``cfg``; return problems."""
    problems: list[str] = []
    if cfg.sheets is None:
        return problems
    try:
        categories = _clean_categories(cfg.sheets.read_categories())
    except Exception as e:  # noqa: BLE001 - keep the bot usable
        categories = []
        problems.append(problem(
            f"cannot read the Config sheet ({_first_line(e)})",
            "check the Config worksheet, then send /reloadcats"))
    else:
        if not categories:
            problems.append(problem(
                "Config sheet has no categories, using " + ", ".join(FALLBACK_CATEGORIES),
                "fill column A of the Config worksheet, then send /reloadcats"))
    cfg.categories = categories or list(FALLBACK_CATEGORIES)
    try:
        cfg.presets = regular.load_presets(cfg.sheets)
    except Exception as e:  # noqa: BLE001 - presets are optional
        cfg.presets = []
        problems.append(problem(
            f"cannot read Regular_expenses ({_first_line(e)})",
            "check the Regular_expenses worksheet, then send /reloadcats"))
    return problems


def load(env: Mapping[str, str], open_sheets: OpenSheets = open_client) -> Config:
    """
    Build the Config from environment values and the spreadsheet.

    Parameters
    ----------
    env : Mapping[str, str]
        Environment values (``os.environ`` in the bot).
    open_sheets : OpenSheets
        Opens the spreadsheet from (creds path, sheet name). Tests pass a fake.

    Returns
    -------
    Config
        Settings; ``problems`` lists each setup mistake as one line.
    """
    problems: list[str] = []
    cfg = Config(allowed_users=_allowed_users(env.get("ALLOWED_USERS"), problems),
                 problems=problems)
    cfg.sheets = _open_sheets(env, open_sheets, problems)
    problems.extend(reload(cfg))
    return cfg
