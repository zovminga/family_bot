"""fx: one exchange-rate source for the bot and the dashboard (HTTP faked)."""
from __future__ import annotations

from typing import Any

import pytest
import requests

from family_bot import fx


class FakeResponse:
    def __init__(self, payload: dict[str, Any], status: int = 200) -> None:
        self.payload, self.status = payload, status

    def raise_for_status(self) -> None:
        if self.status != 200:
            raise requests.HTTPError(f"{self.status} error")

    def json(self) -> dict[str, Any]:
        return self.payload


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, float]]:
    seen: list[tuple[str, float]] = []

    def fake_get(url: str, timeout: float) -> FakeResponse:
        seen.append((url, timeout))
        if url.endswith("/XXX"):
            return FakeResponse({}, status=404)
        return FakeResponse({"rates": {"EUR": 0.0085, "USD": 0.0093}})

    monkeypatch.setattr(fx.requests, "get", fake_get)
    return seen


def test_fetch_rates_includes_base(calls: list[tuple[str, float]]) -> None:
    rates = fx.fetch_rates("RSD")
    assert rates == {"EUR": 0.0085, "USD": 0.0093, "RSD": 1.0}
    assert calls == [("https://api.exchangerate-api.com/v4/latest/RSD", fx.TIMEOUT_SEC)]


def test_fetch_rates_raises_on_http_error(calls: list[tuple[str, float]]) -> None:
    with pytest.raises(requests.HTTPError):
        fx.fetch_rates("XXX")


@pytest.fixture
def fresh_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """A cache that reads through the faked requests.get."""
    monkeypatch.setattr(fx, "RATES", fx.RateCache())


def test_rate_maps_symbols_to_iso(calls: list[tuple[str, float]], fresh_cache: None) -> None:
    # The table is read for the target Currency (RSD); € → дин is its inverse.
    assert fx.rate("€", "дин") == pytest.approx(1 / 0.0085)
    assert calls == [("https://api.exchangerate-api.com/v4/latest/RSD", fx.RATE_TIMEOUT_SEC)]


def test_rate_same_currency_needs_no_request(calls: list[tuple[str, float]],
                                             fresh_cache: None) -> None:
    assert fx.rate("€", "€") == 1.0
    assert calls == []


def test_rate_unknown_or_failed_is_none(calls: list[tuple[str, float]],
                                        fresh_cache: None) -> None:
    assert fx.rate("JPY", "дин") is None
    assert fx.rate("€", "XXX") is None


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def counting_cache(clock: Clock, fail: bool = False) -> tuple[fx.RateCache, list[str]]:
    bases: list[str] = []

    def fetch(base: str) -> dict[str, float]:
        bases.append(base)
        if fail:
            raise requests.ConnectionError("down")
        return {"RSD": 117.0, "USD": 1.1, "EUR": 1.0}

    return fx.RateCache(fetch=fetch, clock=clock), bases


def test_one_request_for_all_pairs_into_one_target(monkeypatch: pytest.MonkeyPatch) -> None:
    cache, bases = counting_cache(Clock())
    monkeypatch.setattr(fx, "RATES", cache)
    assert fx.rate("дин", "€") == pytest.approx(1 / 117.0)
    assert fx.rate("$", "€") == pytest.approx(1 / 1.1)
    assert bases == ["EUR"]


def test_rates_are_cached_for_an_hour(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    cache, bases = counting_cache(clock)
    monkeypatch.setattr(fx, "RATES", cache)
    fx.rate("дин", "€")
    clock.now = fx.RATE_TTL_SEC - 1
    fx.rate("дин", "€")
    assert bases == ["EUR"]
    clock.now = fx.RATE_TTL_SEC + 1
    fx.rate("дин", "€")
    assert bases == ["EUR", "EUR"]


def test_failed_read_is_not_repeated_within_one_request(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    cache, bases = counting_cache(clock, fail=True)
    monkeypatch.setattr(fx, "RATES", cache)
    assert fx.rate("дин", "€") is None
    assert fx.rate("$", "€") is None
    assert bases == ["EUR"]
    clock.now = fx.RETRY_AFTER_FAILURE_SEC + 1
    assert fx.rate("дин", "€") is None
    assert bases == ["EUR", "EUR"]
