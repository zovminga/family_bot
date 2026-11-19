"""Dashboard look: colours, monitor scale, base currency, Category names (findings V5, V6, V10).

The page script runs in Node against the DOM stub from test_dashboard_safety,
with Chart.js replaced by a stub that keeps each chart's config.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from test_dashboard_safety import DASHBOARD_DIR, DOM_STUB, _page_script, _write_csv, bd

node_only = pytest.mark.skipif(
    shutil.which("node") is None, reason="needs Node to run the page script"
)

# Keeps each Chart.js config by canvas id.
CHART_STUB = r"""
const CHARTS = {};
const getEl = document.getElementById;
document.getElementById = (id) => { const e = getEl(id); e.id = id; return e; };
globalThis.Chart = class { constructor(el, cfg) { CHARTS[el.id] = cfg; } destroy() {} resize() {} };
"""

DUMP = r"""
const colors = (id) => CHARTS[id].data.datasets.map(d => ({ label: d.label, bg: d.backgroundColor }));
console.log(JSON.stringify({
  palette: Array.from({ length: 40 }, (_, i) => paletteColor(i)),
  catLabels: CHARTS.catChart.data.labels,
  catColors: CHARTS.catChart.data.datasets[0].backgroundColor,
  stack: colors("stackChart"),
  monitor: ELEMENTS.monitorTable.innerHTML,
  kpis: ELEMENTS.kpis.innerHTML,
  writes: WRITES,
  errors: ERRORS,
}));
"""


RenderPage = Callable[..., dict[str, Any]]


def _row(day: str, category: str, amount: int, cur: str = "дин", comment: str = "") -> list[str]:
    return [day, day[6:] + "-" + day[3:5], category, str(amount), cur, "Azat", comment]


@pytest.fixture
def render_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> RenderPage:
    """Build a page from made-up rows and run its script; return the dump."""
    monkeypatch.setattr(bd, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setattr(bd, "DASHBOARD_DIR", tmp_path)
    monkeypatch.setattr(bd, "get_chartjs", lambda: "/* chart.js stub */")

    def run(rows: list[list[str]], base_iso: str = "RSD") -> dict[str, Any]:
        monkeypatch.setattr(bd, "fetch_rates", lambda base: {"RSD": 1.0, "EUR": 1.0})
        csv_path = tmp_path / "data.csv"
        _write_csv(csv_path, rows)
        out = bd.build(base_iso=base_iso, model="m", use_llm=False,
                       csv_path=str(csv_path), from_month=None)
        runner = tmp_path / "run.js"
        script = _page_script(out.read_text(encoding="utf-8"))
        runner.write_text(DOM_STUB + CHART_STUB + script + DUMP, encoding="utf-8")
        proc = subprocess.run(["node", str(runner)], capture_output=True, text=True,
                              timeout=60, check=True)
        return json.loads(proc.stdout)

    return run


@node_only
def test_palette_never_repeats_a_colour(render_page: RenderPage) -> None:
    result = render_page([_row("01.11.2026", "Food", 100)])
    assert len(set(result["palette"])) == 40


@node_only
def test_each_category_has_its_own_colour_in_every_chart(render_page: RenderPage) -> None:
    rows = [_row("01.11.2026", f"Cat{i:02d}", 100 + i) for i in range(15)]
    rows.append(_row("01.10.2026", "Cat03", 500))
    result = render_page(rows)
    assert result["errors"] == []

    colour = dict(zip(result["catLabels"], result["catColors"], strict=True))
    assert len(colour) == 15
    assert len(set(colour.values())) == 15, "two categories share a colour"
    for ds in result["stack"]:
        assert ds["bg"] == colour[ds["label"]], ds["label"]


def _row_alphas(monitor: str) -> dict[str, list[float]]:
    """Category name -> heat alpha of each month cell (0 for an empty cell)."""
    out: dict[str, list[float]] = {}
    body = monitor.split("<tbody>")[1].split("</tbody>")[0]
    for tr in re.findall(r"<tr>(.*?)</tr>", body, flags=re.S):
        name = re.search(r'<td class="name"[^>]*>(.*?)</td>', tr).group(1)
        cells = re.findall(r'<td class="num heatcell[^"]*"([^>]*)>', tr)
        out[name] = [float(m.group(1)) if (m := re.search(r"--a:([\d.]+)", c)) else 0.0
                     for c in cells]
    return out


@node_only
def test_monitor_colour_is_scaled_per_row(render_page: RenderPage) -> None:
    rows = [
        _row("01.10.2026", "Rent", 100000), _row("01.11.2026", "Rent", 90000),
        _row("05.10.2026", "Coffee", 100), _row("05.11.2026", "Coffee", 200),
    ]
    alphas = _row_alphas(render_page(rows)["monitor"])
    # The biggest month of every row is the brightest, however small the row.
    assert max(alphas["Coffee"]) == max(alphas["Rent"]) == pytest.approx(0.9)
    assert alphas["Coffee"][1] > alphas["Coffee"][0]
    assert alphas["Coffee"][0] == pytest.approx(0.1 + 0.8 * 100 / 200)


def test_template_has_no_hard_coded_base_currency() -> None:
    template = (DASHBOARD_DIR / "template.html").read_text(encoding="utf-8")
    assert "RSD" not in template


@node_only
def test_hints_name_the_base_currency_from_build(render_page: RenderPage) -> None:
    result = render_page([_row("01.11.2026", "Food", 100, cur="€")], base_iso="EUR")
    assert result["errors"] == []
    assert "EUR" in result["kpis"]
    assert not [w for w in result["writes"] if "RSD" in w]


# ---- Category names stay as in the Config sheet (V10) ----

def test_category_order_puts_config_first() -> None:
    order = bd.category_order(["Еда", " Аренда ", "", "Еда"], ["Кафе", "Аренда", "Еда"])
    assert order == ["Еда", "Аренда", "Кафе"]


@pytest.mark.parametrize(
    ("known", "category", "comment", "expected"),
    [
        (["Еда", "Аренда"], "Еда", "аренда за ноябрь", "Аренда"),
        (["Food", "Rent"], "Bills", "Аренда", "Rent"),
        (["Еда"], "Еда", "аренда", "Еда"),  # no rent Category: keep the sheet's
        (["Еда", "Коммуналка"], "", "коммуналка", "Коммуналка"),
        (["Food", "Bills"], "1234", "коммуналка", "Bills"),
        (["Еда"], "", "коммуналка", "Без категории"),
        (["Еда"], "", "хлеб", "Без категории"),
        (["Еда"], "Еда", "хлеб", "Еда"),
    ],
)
def test_repair_category_uses_names_from_the_sheet(
    known: list[str], category: str, comment: str, expected: str
) -> None:
    assert bd.repair_category(category, comment, known) == expected


def test_build_lists_categories(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bd, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setattr(bd, "DASHBOARD_DIR", tmp_path)
    monkeypatch.setattr(bd, "fetch_rates", lambda base: {"RSD": 1.0})
    monkeypatch.setattr(bd, "get_chartjs", lambda: "")
    csv_path = tmp_path / "data.csv"
    _write_csv(csv_path, [_row("01.11.2026", "Кафе", 1), _row("02.11.2026", "Еда", 2),
                          _row("03.11.2026", "Еда", 3, comment="аренда")])
    out = bd.build(base_iso="RSD", model="m", use_llm=False, csv_path=str(csv_path),
                   from_month=None)
    data = json.loads(re.search(r"const DATA = (.*);\n", out.read_text(encoding="utf-8")).group(1))
    assert data["categories"] == ["Еда", "Кафе"]
    assert [r["category"] for r in data["rows"]] == ["Кафе", "Еда", "Еда"]
