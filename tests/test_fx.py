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


def test_rate_maps_symbols_to_iso(calls: list[tuple[str, float]]) -> None:
    assert fx.rate("дин", "€") == 0.0085
    assert calls == [("https://api.exchangerate-api.com/v4/latest/RSD", fx.RATE_TIMEOUT_SEC)]


def test_rate_same_currency_needs_no_request(calls: list[tuple[str, float]]) -> None:
    assert fx.rate("€", "€") == 1.0
    assert calls == []


def test_rate_unknown_or_failed_is_none(calls: list[tuple[str, float]]) -> None:
    assert fx.rate("дин", "JPY") is None
    assert fx.rate("XXX", "€") is None
