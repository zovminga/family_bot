"""Dashboard: Large expenses stay out of every calculation and get their own block (#88).

Builds run end to end from a made-up CSV with FX rates and Chart.js stubbed;
the page script runs in Node against the DOM stub from test_dashboard_safety.
"""

from __future__ import annotations

import csv
import json
import re
import shutil
import subprocess
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from test_dashboard_safety import DOM_STUB, _page_script

from family_bot.dashboard import build_dashboard as bd

AS_OF = date(2026, 11, 19)
HEADERS = ["Date", "Month", "Category", "Amount", "Currency", "Who", "Comment", "Large"]

node_only = pytest.mark.skipif(
    shutil.which("node") is None, reason="needs Node to run the page script"
)

Build = Callable[..., str]


def _row(day: str, category: str, amount: int, cur: str = "дин",
         comment: str = "", large: str = "") -> list[str]:
    return [day, day[6:] + "-" + day[3:5], category, str(amount), cur, "Ann", comment, large]


ROUTINE = [
    _row("01.09.2026", "Rent", 1000),
    _row("10.09.2026", "Food", 300),
    _row("01.10.2026", "Rent", 1000),
    _row("12.10.2026", "Food", 400),
    _row("01.11.2026", "Rent", 1000),
    _row("05.11.2026", "Food", 200),
]
LARGE = [
    _row("20.09.2026", "Travel", 2000, "€", "билеты", "да"),
    _row("03.11.2026", "Tech", 150000, "дин", "ноутбук", "да"),
    _row("15.10.2026", "Travel", 90000, "дин", "", "крупная"),
]


@pytest.fixture
def build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Build:
    """Run bd.build on made-up rows under ``headers``; return the output HTML."""
    monkeypatch.setattr(bd, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setattr(bd, "DASHBOARD_DIR", tmp_path)
    monkeypatch.setattr(bd, "fetch_rates", lambda base: {"RSD": 1.0, "EUR": 0.0085})
    monkeypatch.setattr(bd, "get_chartjs", lambda: "/* chart.js stub */")

    def run(rows: list[list[str]], headers: list[str] = HEADERS, **kwargs: Any) -> str:
        csv_path = tmp_path / "data.csv"
        with csv_path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(headers)
            writer.writerows([r[:len(headers)] for r in rows])
        out = bd.build(base_iso="RSD", model="m", use_llm=False, csv_path=str(csv_path),
                       as_of=AS_OF, **kwargs)
        return out.read_text(encoding="utf-8")

    return run


def _data(html: str) -> dict[str, Any]:
    match = re.search(r"const DATA = (.*);\n", _page_script(html))
    assert match
    data: dict[str, Any] = json.loads(match.group(1))
    return data


DUMP = r"""
const grab = () => ({
  month: ELEMENTS.thisMonth.innerHTML,
  cats: ELEMENTS.thisMonthCats.innerHTML,
  kpis: ELEMENTS.kpis.innerHTML,
  insights: ELEMENTS.insights.innerHTML,
  monitor: ELEMENTS.monitorTable.innerHTML,
  opt: ELEMENTS.optSummary.innerHTML,
  takeaway: ELEMENTS.optTakeaway.innerHTML,
  table: ELEMENTS["#txTable tbody"].innerHTML,
});
console.log(JSON.stringify({
  page: grab(),
  large: ELEMENTS["#largeTable tbody"]?.innerHTML ?? null,
  largeShown: ELEMENTS.largeCard?.style.display ?? "none",
  errors: ERRORS,
}));
"""


def _run_page(html: str, tmp_path: Path) -> dict[str, Any]:
    runner = tmp_path / "run.js"
    runner.write_text(DOM_STUB + _page_script(html) + DUMP, encoding="utf-8")
    proc = subprocess.run(["node", str(runner)], capture_output=True, text=True,
                          timeout=60, check=True)
    # Intl.NumberFormat("ru-RU") groups digits with a no-break space.
    result: dict[str, Any] = json.loads(re.sub(r"[  ]", " ", proc.stdout))
    return result


def test_large_rows_kept_out_of_rows(build: Build) -> None:
    data = _data(build(ROUTINE + LARGE))
    assert all(r["category"] not in ("Travel", "Tech") for r in data["rows"])
    assert len(data["rows"]) == len(ROUTINE)


def test_large_block_lists_newest_first(build: Build) -> None:
    data = _data(build(ROUTINE + LARGE))
    assert [(r["date"], r["category"], r["amount"], r["currency_symbol"], r["comment"])
            for r in data["large"]] == [
        ("03.11.2026", "Tech", 150000.0, "дин", "ноутбук"),
        ("15.10.2026", "Travel", 90000.0, "дин", ""),
        ("20.09.2026", "Travel", 2000.0, "€", "билеты"),
    ]
    assert data["large"][2]["amount_base"] == round(2000 / 0.0085, 2)


def test_no_large_column_builds_as_before(build: Build) -> None:
    without = _data(build(ROUTINE, headers=HEADERS[:-1]))
    marked_none = _data(build(ROUTINE))
    assert without["large"] == []
    assert without["rows"] == marked_none["rows"]


def test_unmarked_cells_count_as_routine(build: Build) -> None:
    data = _data(build(ROUTINE + [_row("06.11.2026", "Food", 50, large="нет")]))
    assert data["large"] == []
    assert len(data["rows"]) == len(ROUTINE) + 1


def test_cash_withdrawal_marked_large_stays_dropped(build: Build) -> None:
    data = _data(build(ROUTINE + [_row("07.11.2026", "Cash", 50000,
                                       comment="снятие наличных", large="да")]))
    assert data["large"] == []


def test_from_month_hides_old_large_expenses(build: Build) -> None:
    data = _data(build(ROUTINE + LARGE, from_month="2026-10"))
    assert [r["date"] for r in data["large"]] == ["03.11.2026", "15.10.2026"]


@node_only
def test_every_calculation_ignores_large(build: Build, tmp_path: Path) -> None:
    with_large = _run_page(build(ROUTINE + LARGE), tmp_path)
    routine_only = _run_page(build(ROUTINE), tmp_path)
    assert with_large["errors"] == []
    assert with_large["page"] == routine_only["page"]


@node_only
def test_large_block_shown_with_rows(build: Build, tmp_path: Path) -> None:
    result = _run_page(build(ROUTINE + LARGE), tmp_path)
    assert result["largeShown"] == ""
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", result["large"]))
    assert text.index("03.11.2026") < text.index("15.10.2026") < text.index("20.09.2026")
    assert "150 000 дин" in text and "ноутбук" in text
    assert "2 000 € ≈ 235 294 дин билеты" in text


@node_only
def test_no_block_without_large(build: Build, tmp_path: Path) -> None:
    result = _run_page(build(ROUTINE), tmp_path)
    assert result["largeShown"] == "none"
    assert not result["large"]
