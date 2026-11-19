"""Currency registry and the places it feeds (issue #14)."""
from __future__ import annotations

import json
import sys
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest

from family_bot import currency
from family_bot import photo_input as px
from family_bot import regular
from family_bot import sheet_data
from family_bot import voice_input as vx


def test_legacy_currencies_keep_their_symbols() -> None:
    stored = {c.code: c.symbol for c in currency.CURRENCIES}
    assert stored == {"RSD": "дин", "EUR": "€", "RUB": "₽", "JPY": "¥", "GBP": "£",
                      "USD": "$", "CNY": "CNY"}


@pytest.mark.parametrize("value, symbol", [
    ("€", "€"), ("EUR", "€"), ("eur", "€"), (" $ ", "$"), ("USD", "$"),
    ("¥", "¥"), ("JPY", "¥"), ("CNY", "CNY"), ("cny", "CNY"),
    ("доллар", "$"), ("Юаней", "CNY"), ("иена", "¥"),
])
def test_find_understands_symbols_codes_and_words(value: str, symbol: str) -> None:
    found = currency.find(value)
    assert found is not None and found.symbol == symbol


@pytest.mark.parametrize("value", ["", None, "XYZ", "руб.."])
def test_find_unknown_is_none(value: Any) -> None:
    assert currency.find(value) is None


@pytest.mark.parametrize("value, iso", [
    ("¥", "JPY"), ("$", "USD"), ("дин", "RSD"), ("CNY", "CNY"), ("EUR", "EUR"),
    ("gel", "GEL"), ("", ""),
])
def test_to_iso(value: str, iso: str) -> None:
    assert currency.to_iso(value) == iso
    assert sheet_data.to_iso(value) == iso


def test_sheet_data_maps_come_from_registry() -> None:
    assert sheet_data.SYMBOL_TO_ISO["¥"] == "JPY"
    assert sheet_data.SYMBOL_TO_ISO["$"] == "USD"
    assert sheet_data.ISO_TO_SYMBOL["CNY"] == "CNY"


def test_bot_buttons_are_the_registry() -> None:
    from family_bot import config
    buttons = config.Config().currencies
    assert buttons == currency.symbols()
    assert "$" in buttons and "CNY" in buttons


@pytest.mark.parametrize("value, expected", [
    ("$", "$"), ("USD", "$"), ("CNY", "CNY"), ("юань", "CNY"), ("¥", "¥"),
    ("XYZ", "дин"), (None, "дин"),
])
def test_snap_currency_normalises_to_stored_symbol(value: Any, expected: str) -> None:
    assert vx._snap_currency(value, currency.symbols(), "дин") == expected


def test_voice_prompt_maps_spoken_words_from_registry() -> None:
    prompt = vx._build_prompt("сто юаней", ["Food"], currency.symbols(),
                              "01.11.2026", "Azat", "дин")
    assert "юань/юаней→CNY" in prompt
    assert "доллар/долларов→$" in prompt
    assert "иена/йен→¥" in prompt


def test_receipt_prompt_lists_codes_from_registry() -> None:
    prompt = px._build_prompt(["Food"], currency.symbols(), "01.11.2026", "Azat", "дин")
    assert "USD→$" in prompt
    assert "JPY→¥" in prompt
    assert "CNY→CNY" in prompt


class _FakeAnthropic:
    """Stands in for anthropic.Anthropic; returns a canned JSON reply."""

    reply: ClassVar[dict[str, Any]] = {}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs: Any) -> SimpleNamespace:
        block = SimpleNamespace(type="text", text=json.dumps(self.reply, ensure_ascii=False))
        return SimpleNamespace(content=[block])


@pytest.fixture
def fake_claude(monkeypatch: pytest.MonkeyPatch) -> type[_FakeAnthropic]:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(Anthropic=_FakeAnthropic))
    return _FakeAnthropic


@pytest.mark.parametrize("claude_currency, stored", [
    ("$", "$"), ("USD", "$"), ("CNY", "CNY"), ("¥", "¥"),
])
def test_voice_extract_keeps_currency(fake_claude: type[_FakeAnthropic],
                                      claude_currency: str, stored: str) -> None:
    fake_claude.reply = {"expenses": [{"comment": "x", "amount": 10, "currency": claude_currency,
                                       "date": "01.11.2026", "category": "Food"}]}
    out = vx.extract_expenses("десять", ["Food", "Other"], currency.symbols(),
                              "01.11.2026", "Azat", "дин")
    assert out["expenses"][0]["currency"] == stored


def test_receipt_extract_keeps_dollars(fake_claude: type[_FakeAnthropic]) -> None:
    fake_claude.reply = {"currency": "USD", "date": "01.11.2026",
                         "items": [{"name": "milk", "amount": 10, "category": "Food",
                                    "group": "Продукты"}]}
    out = px.extract_receipt(b"img", "image/jpeg", ["Food", "Other"], currency.symbols(),
                             "01.11.2026", "Azat", "дин")
    assert out["expenses"][0]["currency"] == "$"


def test_regular_currency_columns_come_from_registry() -> None:
    grid = [
        ["название", "динары", "евро", "доллары", "рубли", "юани", "категория", "кто"],
        ["ежемесячные траты", "", "", "", "", "", "", ""],
        ["Rent", "", "500", "", "", "", "Rent", "Both"],
        ["VPN", "", "", "5", "", "", "Other", "Azat"],
        ["Tea", "", "", "", "", "30", "Food", "Lisa"],
    ]
    items = regular.parse_regular(grid)["monthly"]
    assert [i["currency"] for i in items] == ["€", "$", "CNY"]


def test_regular_header_word_containing_a_stem_is_not_a_currency() -> None:
    grid = [
        ["название", "клиенты", "евро", "категория", "кто"],
        ["Gift", "7", "", "Other", "Both"],
    ]
    assert regular.parse_regular(grid)["monthly"] == []


@pytest.mark.parametrize("amount, symbol, text", [
    (350, "дин", "350 дин"),
    (350.0, "дин", "350 дин"),
    (1234, "дин", "1 234 дин"),
    (1234567.0, "€", "1 234 567 €"),
    (12.5, "€", "12,50 €"),
    (1234.05, "$", "1 234,05 $"),
    (0.285, "€", "0,29 €"),  # rounds half up, not down through float error
    (999.999, "дин", "1 000 дин"),
    (-1500, "дин", "-1 500 дин"),
    (1234.5, None, "1 234,50"),
])
def test_format_amount(amount: float, symbol: str | None, text: str) -> None:
    assert currency.format_amount(amount, symbol) == text
