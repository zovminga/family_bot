"""Dashboard «По каждой» mode: different currencies are never added together (finding V3).

The page script runs in Node against the DOM stub from test_dashboard_safety.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest
from test_dashboard_safety import DOM_STUB, _page_script, build_from_rows  # noqa: F401

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="needs Node to run the page script"
)

ROWS = [
    ["01.11.2026", "2026-11", "Food", "100", "дин", "Azat", "хлеб"],
    ["02.11.2026", "2026-11", "Food", "50", "€", "Lisa", "сыр"],
    ["03.10.2026", "2026-10", "Fun", "30", "€", "Lisa", "кино"],
]

# Render in native mode twice: no currency chosen, then EUR chosen.
DUMP = r"""
const grab = () => ({
  kpis: ELEMENTS.kpis.innerHTML,
  monitor: ELEMENTS.monitorTable.innerHTML,
  insights: ELEMENTS.insights.innerHTML,
  grouping: ELEMENTS.grouping.innerHTML,
});
mode = "native"; render();
const mixed = grab();
openDrillRows("Все", DATA.rows);
mixed.drill = ELEMENTS.drillSummary.innerHTML + ELEMENTS.drillTable.innerHTML;
ELEMENTS.fCur.value = "EUR"; render();
console.log(JSON.stringify({ mixed, eur: grab(), errors: ERRORS }));
"""


@pytest.fixture
def rendered(build_from_rows, tmp_path):  # noqa: F811
    html = build_from_rows(ROWS)
    runner = tmp_path / "run.js"
    runner.write_text(DOM_STUB + _page_script(html) + DUMP, encoding="utf-8")
    proc = subprocess.run(
        ["node", str(runner)], capture_output=True, text=True, timeout=60, check=True
    )
    return json.loads(proc.stdout)


def test_no_currency_chosen_shows_each_currency_total(rendered):
    assert rendered["errors"] == []
    mixed = rendered["mixed"]
    for part in ("kpis", "monitor", "insights", "grouping", "drill"):
        html = mixed[part]
        assert "100 дин" in html, part
        assert all(s not in html for s in ("150", "180", "130")), (
            f"{part} adds дин and €"
        )
    assert "80 €" in mixed["kpis"]
    assert "80 €" in mixed["monitor"]  # Σ / Итого of the Food+Fun rows in €


def test_one_currency_chosen_shows_plain_sum(rendered):
    eur = rendered["eur"]["kpis"]
    assert "Всего (EUR)" in eur
    assert ">80<" in eur  # 50 € + 30 €, a plain number under the EUR label
