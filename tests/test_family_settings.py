"""Household data comes from settings, not code (I8): base currency, first month,
Spender aliases, local theme keywords."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from family_bot import config
from family_bot.dashboard import build_dashboard as bd


# ---------- bot: base currency ----------

def test_bot_default_currency_comes_from_base_currency() -> None:
    cfg = config.load({"ALLOWED_USERS": "111:Ann", "BASE_CURRENCY": "rsd"})
    assert cfg.default_currency == "дин"


def test_bot_default_currency_without_setting_is_neutral() -> None:
    cfg = config.load({"ALLOWED_USERS": "111:Ann"})
    assert cfg.default_currency == "€"


def test_unknown_base_currency_is_a_setup_problem() -> None:
    cfg = config.load({"ALLOWED_USERS": "111:Ann", "BASE_CURRENCY": "XYZ"})
    assert cfg.default_currency == "€"
    assert any("BASE_CURRENCY" in p and " — " in p for p in cfg.problems)


# ---------- dashboard: Spender aliases ----------

def test_spender_aliases_parse() -> None:
    assert bd.parse_spender_aliases(" ann = Анна, Annie=Анна ,") == {
        "ann": "Анна", "annie": "Анна"}
    assert bd.parse_spender_aliases(None) == {}


def test_spender_aliases_reject_item_without_name() -> None:
    with pytest.raises(ValueError, match="DASHBOARD_SPENDER_ALIASES"):
        bd.parse_spender_aliases("ann")


def test_normalize_spender_uses_aliases_and_keeps_others() -> None:
    aliases = {"annie": "Анна"}
    assert bd.normalize_spender(" Annie ", aliases) == "Анна"
    assert bd.normalize_spender(" Bob ", aliases) == "Bob"
    assert bd.normalize_spender("", aliases) == "—"


# ---------- dashboard: local theme keywords ----------

def test_theme_keywords_file_adds_words_to_existing_themes(tmp_path: Path) -> None:
    path = tmp_path / "theme_keywords.json"
    path.write_text(json.dumps({"Кофе": ["corner cafe"]}, ensure_ascii=False))
    rules = bd.load_theme_rules(path)
    assert bd.classify_theme("Corner Cafe latte", rules) == "Кофе"
    assert bd.classify_theme("Corner Cafe", bd.THEME_RULES) is None
    assert [t for t, _ in rules] == [t for t, _ in bd.THEME_RULES]


def test_theme_keywords_missing_file_keeps_code_rules(tmp_path: Path) -> None:
    assert bd.load_theme_rules(tmp_path / "none.json") == bd.THEME_RULES


@pytest.mark.parametrize("content", [
    json.dumps({"Нет такой темы": ["x"]}, ensure_ascii=False),
    json.dumps({"Кофе": "not a list"}, ensure_ascii=False),
    json.dumps(["Кофе"]),
])
def test_theme_keywords_bad_file_fails(tmp_path: Path, content: str) -> None:
    path = tmp_path / "theme_keywords.json"
    path.write_text(content)
    with pytest.raises(ValueError, match="theme_keywords.json"):
        bd.load_theme_rules(path)


# ---------- dashboard: CLI defaults from .env ----------

@pytest.fixture
def run_main(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    for name in ("BASE_CURRENCY", "DASHBOARD_FROM_MONTH", "DASHBOARD_SPENDER_ALIASES"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(bd, "THEME_KEYWORDS_PATH", tmp_path / "none.json")
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(bd, "build", lambda **kw: calls.append(kw) or tmp_path / "x.html")

    def run(*argv: str) -> dict[str, Any]:
        monkeypatch.setattr(sys, "argv", ["build_dashboard", *argv])
        bd.main()
        return calls[-1]
    return run


def test_main_defaults_are_neutral(run_main: Any) -> None:
    kw = run_main()
    assert kw["base_iso"] == "EUR"
    assert kw["from_month"] is None
    assert kw["spender_aliases"] == {}


def test_main_reads_base_and_first_month_from_env(
        run_main: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BASE_CURRENCY", "RSD")
    monkeypatch.setenv("DASHBOARD_FROM_MONTH", "2024-03")
    kw = run_main()
    assert kw["base_iso"] == "RSD"
    assert kw["from_month"] == "2024-03"
    assert run_main("--from", "", "--base", "USD")["from_month"] is None
    assert run_main("--base", "USD")["base_iso"] == "USD"


def test_main_rejects_bad_first_month(run_main: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_FROM_MONTH", "2024/03")
    with pytest.raises(SystemExit):
        run_main()


def test_main_passes_spender_aliases(run_main: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_SPENDER_ALIASES", "annie=Анна")
    assert run_main()["spender_aliases"] == {"annie": "Анна"}


def test_main_bad_spender_aliases_fail_before_build(
        run_main: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_SPENDER_ALIASES", "annie")
    with pytest.raises(ValueError, match="DASHBOARD_SPENDER_ALIASES"):
        run_main()
