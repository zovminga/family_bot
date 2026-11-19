"""Dashboard first screen: this month, usual level, forecast (findings V2, V4, V8).

The build date is fixed at 2026-11-19: November is an unfinished month of
19 days out of 30. History months are August, September and October.
The page script runs in Node against the DOM stub from test_dashboard_safety.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import date

import pytest
from test_dashboard_safety import DOM_STUB, _page_script, build_from_rows  # noqa: F401

AS_OF = date(2026, 11, 19)

node_only = pytest.mark.skipif(
    shutil.which("node") is None, reason="needs Node to run the page script"
)


def _row(day: str, category: str, amount: int, cur: str = "дин") -> list[str]:
    return [day, day[6:] + "-" + day[3:5], category, str(amount), cur, "Azat", ""]


# Per month (RSD): Rent on the 1st, Food and Fun inside and after day 19.
ROWS = [
    _row("01.07.2026", "Rent", 5000),  # 4 months back: not in the 3-month history
    _row("01.08.2026", "Rent", 1000),
    _row("10.08.2026", "Food", 300),
    _row("25.08.2026", "Food", 600),
    _row("01.09.2026", "Rent", 1000),
    _row("05.09.2026", "Food", 600),
    _row("28.09.2026", "Food", 300),
    _row("01.10.2026", "Rent", 1000),
    _row("15.10.2026", "Food", 300),
    _row("20.10.2026", "Fun", 600),
    _row("01.11.2026", "Rent", 1000),
    _row("03.11.2026", "Food", 800),
    _row("18.11.2026", "Fun", 100),
]

DUMP = r"""
const grab = () => ({
  month: ELEMENTS.thisMonth.innerHTML,
  cats: ELEMENTS.thisMonthCats.innerHTML,
  kpis: ELEMENTS.kpis.innerHTML,
  insights: ELEMENTS.insights.innerHTML,
  monitor: ELEMENTS.monitorTable.innerHTML,
  takeaway: ELEMENTS.optTakeaway.innerHTML,
});
const base = grab();
mode = "native"; render();
console.log(JSON.stringify({ base, native: grab(), errors: ERRORS }));
"""


def _run(build_from_rows, tmp_path, rows):  # noqa: F811
    html = build_from_rows(rows, as_of=AS_OF)
    runner = tmp_path / "run.js"
    runner.write_text(DOM_STUB + _page_script(html) + DUMP, encoding="utf-8")
    proc = subprocess.run(
        ["node", str(runner)], capture_output=True, text=True, timeout=60, check=True
    )
    out = json.loads(proc.stdout)
    # Intl.NumberFormat("ru-RU") groups digits with a no-break space.
    return json.loads(re.sub(r"[  ]", " ", json.dumps(out, ensure_ascii=False)))


def _text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


@pytest.fixture
def page(build_from_rows, tmp_path):  # noqa: F811
    return _run(build_from_rows, tmp_path, ROWS)


def test_build_stamps_as_of_date(build_from_rows):  # noqa: F811
    html = build_from_rows(ROWS, as_of=AS_OF)
    data = json.loads(re.search(r"const DATA = (.*);\n", _page_script(html)).group(1))
    assert data["as_of"] == "2026-11-19"


def test_as_of_defaults_to_today(build_from_rows):  # noqa: F811
    html = build_from_rows(ROWS)
    data = json.loads(re.search(r"const DATA = (.*);\n", _page_script(html)).group(1))
    assert data["as_of"] == date.today().isoformat()


@node_only
def test_first_card_this_month_usual_forecast(page):
    assert page["errors"] == []
    card = _text(page["base"]["month"])
    # X = 1000 + 800 + 100. Usual = (1900 + 1900 + 1900) / 3, July is not counted.
    assert "Этот месяц" in card
    assert "1 900 дин" in card
    assert "обычно 1 900 дин" in card
    # Forecast = 1900 + mean spent after day 19 in Aug–Oct: (600 + 300 + 600) / 3.
    assert "прогноз 2 400 дин" in card
    assert "19 из 30 дней" in card


@node_only
def test_unfinished_month_compared_with_same_days(page):
    card = _text(page["base"]["month"])
    # October 1–19 = 1300 (not the full 1900): 1900 / 1300 − 1 = +46%.
    assert "▲ 46% к 1–19 октября" in card


@node_only
def test_top_categories_against_three_month_average(page):
    cats = _text(page["base"]["cats"])
    # Food by day 19: 800 now, (300 + 600 + 300) / 3 = 400 usually.
    assert re.search(r"Food 800 дин обычно 400 дин ▲ 100%", cats), cats
    assert re.search(r"Rent 1 000 дин обычно 1 000 дин", cats), cats
    # Order: by this month's sum.
    assert cats.index("Rent") < cats.index("Food") < cats.index("Fun")


@node_only
def test_hints_do_not_praise_a_drop_from_an_unfinished_month(page):
    base = page["base"]
    # Full October Fun (600) vs November 1–19 (100) would read as a drop.
    assert "упал" not in base["insights"]
    assert "так держать" not in base["takeaway"]
    assert "trend-down" not in base["monitor"]
    assert "1–19 ноября" in _text(base["insights"])


@node_only
def test_old_current_month_kpi_is_gone(page):
    assert "Текущий месяц" not in page["base"]["kpis"]


@node_only
def test_history_shorter_than_three_months(build_from_rows, tmp_path):  # noqa: F811
    rows = [r for r in ROWS if r[1] >= "2026-10"]
    card = _text(_run(build_from_rows, tmp_path, rows)["base"]["month"])
    # Only October is history: usual 1900, forecast 1900 + 600.
    assert "обычно 1 900 дин" in card
    assert "прогноз 2 500 дин" in card
    assert "за 1 мес." in card


@node_only
def test_no_history_shows_only_this_month(build_from_rows, tmp_path):  # noqa: F811
    rows = [r for r in ROWS if r[1] == "2026-11"]
    card = _text(_run(build_from_rows, tmp_path, rows)["base"]["month"])
    assert "1 900 дин" in card
    assert "обычно" not in card
    assert "прогноз" not in card


@node_only
def test_per_currency_never_added(build_from_rows, tmp_path):  # noqa: F811
    rows = ROWS + [_row("02.10.2026", "Fun", 30, "€"), _row("02.11.2026", "Fun", 40, "€")]
    page = _run(build_from_rows, tmp_path, rows)
    assert page["errors"] == []
    card = _text(page["native"]["month"])
    assert "1 900 дин" in card and "40 €" in card
    # € usual = 30 / 3 = 10, forecast € = 40 (nothing after day 19).
    # Currencies are listed by their base-currency weight, € first here.
    assert "обычно 10 € · 1 900 дин" in card
    assert "прогноз 40 € · 2 400 дин" in card
    cats = _text(page["native"]["cats"])
    assert "40 €" in cats
    for total in ("1 940", "1 910", "2 440"):
        assert total not in card, f"{total}: дин and € added"
