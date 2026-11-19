"""Currency picker: Recent currencies, «🌍 Другая» pages, search (issue #33)."""
from __future__ import annotations

from typing import Any

import pytest
import requests

from family_bot import currency_picker as picker
from tests.conftest import FakeSheetsClient

CODES = ["AED", "CAD", "CNY", "EUR", "GBP", "GEL", "IQD", "JPY", "RSD", "RUB", "TRY", "USD"]
HEADERS = ["Date", "Month", "Category", "Amount", "Currency", "Who", "Comment"]


def data_rows(*currencies: str) -> list[list[str]]:
    return [HEADERS] + [["01.10.2026", "2026-10", "Еда", "1", c, "Анна", ""]
                        for c in currencies]


def cb(payload: str) -> str:
    return f"x:{payload}"


def labels(grid: list[list[tuple[str, str]]]) -> list[str]:
    return [label for row in grid for label, _ in row]


# --- search ---

@pytest.mark.parametrize("text, codes", [
    ("юань", ["CNY"]), ("GEL", ["GEL"]), ("gel", ["GEL"]), ("лари", ["GEL"]),
    ("дин", ["RSD"]), ("€", ["EUR"]), ("иракский динар", ["IQD"]),
    ("канадский", ["CAD"]), ("ЮАНЕЙ", ["CNY"]),
])
def test_search_finds_by_code_symbol_or_russian_name(text: str, codes: list[str]) -> None:
    assert picker.search(text, CODES) == codes


def test_search_lists_every_match() -> None:
    assert picker.search("лира", CODES) == ["TRY"]
    found = picker.search("долл", CODES)
    assert "CAD" in found and "USD" in found


@pytest.mark.parametrize("text", ["", "  ", "xyz", "ab", "кофе"])
def test_search_without_match_is_empty(text: str) -> None:
    assert picker.search(text, CODES) == []


def test_search_ignores_codes_the_rate_service_does_not_have() -> None:
    assert picker.search("лари", ["EUR", "USD"]) == []


# --- buttons ---

def test_recent_grid_has_flags_and_other_button() -> None:
    grid = picker.recent_grid(["дин", "€", "CNY"], cb)
    assert grid[0] == [("🇷🇸 дин", "x:pick:RSD"), ("🇪🇺 €", "x:pick:EUR"),
                       ("🇨🇳 CNY", "x:pick:CNY")]
    assert grid[1] == [(picker.OTHER_BUTTON, "x:page:0")]


def test_pages_cover_all_codes_in_code_order() -> None:
    codes = [f"A{a}{b}" for a in "ABCDEFGHIJ" for b in "ABCDE"]  # 50 codes
    pages = picker.page_count(codes)
    assert pages == 3
    seen = []
    for page in range(pages):
        grid = picker.page_grid(codes, page, cb)
        seen += [data.removeprefix("x:pick:") for row in grid for _, data in row
                 if data.startswith("x:pick:")]
    assert seen == sorted(codes)


def test_page_arrows_wrap_around() -> None:
    codes = [f"A{a}{b}" for a in "ABCDEFGHIJ" for b in "ABCDE"]
    nav_row = picker.page_grid(codes, 0, cb)[-1]
    assert nav_row == [("◀️", "x:page:2"), ("▶️", "x:page:1")]


def test_one_page_has_no_arrows() -> None:
    grid = picker.page_grid(CODES, 0, cb)
    assert all(not data.startswith("x:page:") for row in grid for _, data in row)


def test_page_grid_shows_stored_values() -> None:
    assert "🇷🇸 дин" in labels(picker.page_grid(CODES, 0, cb))


@pytest.mark.parametrize("payload, code", [("pick:RSD", "RSD"), ("pick:gel", "GEL"),
                                           ("page:1", None), ("pick:", None), ("x", None)])
def test_picked_code(payload: str, code: str | None) -> None:
    assert picker.picked_code(payload) == code


@pytest.mark.parametrize("payload, page", [("page:0", 0), ("page:3", 3), ("page:x", None),
                                           ("pick:RSD", None)])
def test_page_number(payload: str, page: int | None) -> None:
    assert picker.page_number(payload) == page


# --- Recent currencies ---

def test_push_moves_choice_to_front_and_keeps_three() -> None:
    assert picker.push(["дин", "€", "₽"], "CNY") == ["CNY", "дин", "€"]
    assert picker.push(["дин", "€", "₽"], "₽") == ["₽", "дин", "€"]


def test_new_user_gets_three_most_frequent_in_data() -> None:
    sheets = FakeSheetsClient(data_values=data_rows("€", "$", "$", "₽", "$", "€", "дин"))
    assert picker.load_recent({}, sheets, 111) == ["$", "€", "₽"]


def test_new_user_with_empty_data_gets_defaults() -> None:
    assert picker.load_recent({}, FakeSheetsClient(), 111) == ["дин", "€", "₽"]


def test_few_currencies_in_data_are_topped_up_with_defaults() -> None:
    sheets = FakeSheetsClient(data_values=data_rows("евро", "GEL", "GEL"))
    assert picker.load_recent({}, sheets, 111) == ["GEL", "€", "дин"]


def test_saved_recent_wins_over_data_and_is_cached() -> None:
    sheets = FakeSheetsClient(data_values=data_rows("€"))
    sheets.users[111] = ["CNY", "дин", "€"]
    user_data: dict[str, Any] = {}
    assert picker.load_recent(user_data, sheets, 111) == ["CNY", "дин", "€"]
    sheets.users[111] = ["₽"]
    assert picker.load_recent(user_data, sheets, 111) == ["CNY", "дин", "€"]


def test_recent_is_per_user() -> None:
    sheets = FakeSheetsClient()
    sheets.users[111] = ["CNY", "дин", "€"]
    assert picker.load_recent({}, sheets, 222) == ["дин", "€", "₽"]


def test_remember_writes_the_users_sheet() -> None:
    sheets = FakeSheetsClient()
    user_data: dict[str, Any] = {}
    picker.remember(user_data, sheets, 111, "Анна", "CNY")
    assert sheets.users[111] == ["CNY", "дин", "€"]
    assert sheets.user_names[111] == "Анна"
    assert picker.load_recent(user_data, sheets, 111) == ["CNY", "дин", "€"]


def test_remember_same_first_currency_writes_nothing() -> None:
    sheets = FakeSheetsClient()
    user_data: dict[str, Any] = {picker.KEY_RECENT: ["дин", "€", "₽"]}
    picker.remember(user_data, sheets, 111, "Анна", "дин")
    assert sheets.users == {}


def test_failed_write_keeps_the_choice_in_memory() -> None:
    class Broken(FakeSheetsClient):
        def write_recent_currencies(self, user_id: int, name: str, values: list[str]) -> None:
            raise RuntimeError("APIError 503")

    user_data: dict[str, Any] = {}
    picker.remember(user_data, Broken(), 111, "Анна", "CNY")
    assert user_data[picker.KEY_RECENT] == ["CNY", "дин", "€"]


def test_failed_read_falls_back_to_defaults_and_retries_later() -> None:
    class Broken(FakeSheetsClient):
        def read_recent_currencies(self, user_id: int) -> list[str] | None:
            raise RuntimeError("APIError 503")

    user_data: dict[str, Any] = {}
    assert picker.load_recent(user_data, Broken(), 111) == ["дин", "€", "₽"]
    assert picker.KEY_RECENT not in user_data


def test_no_sheets_gives_defaults() -> None:
    assert picker.load_recent({}, None, 111) == ["дин", "€", "₽"]


# --- rate service catalog ---

def test_catalog_sorts_codes_adds_registry_and_caches() -> None:
    calls = []

    def fetch() -> list[str]:
        calls.append(1)
        return ["GEL", "AED", "EUR"]

    now = [0.0]
    catalog = picker.Catalog(fetch=fetch, clock=lambda: now[0])
    codes = catalog.codes()
    assert codes == sorted(codes)
    assert {"AED", "GEL", "RSD", "CNY"} <= set(codes)
    catalog.codes()
    assert len(calls) == 1
    now[0] += picker.CATALOG_TTL_SEC + 1
    catalog.codes()
    assert len(calls) == 2


def test_catalog_falls_back_to_registry_when_service_fails() -> None:
    def fetch() -> list[str]:
        raise requests.ConnectionError("down")

    codes = picker.Catalog(fetch=fetch).codes()
    assert codes == sorted(["CNY", "EUR", "GBP", "JPY", "RSD", "RUB", "USD"])
