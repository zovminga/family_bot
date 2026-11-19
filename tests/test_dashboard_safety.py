"""Dashboard build safety: sheet text must stay text in the HTML (finding D1).

The build is run end to end from a made-up CSV with FX rates and Chart.js
stubbed. The page script is then executed in Node against a minimal DOM stub
that records every innerHTML write.
"""
from __future__ import annotations

import csv
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import pytest

from family_bot.dashboard import build_dashboard as bd

XSS = '"><img src=x onerror=alert(1)>'
SCRIPT_CLOSE = "</script><script>alert(2)</script>"
HEADERS = ["Date", "Month", "Category", "Amount", "Currency", "Who", "Comment"]

# Minimal DOM: every element records innerHTML writes into WRITES.
DOM_STUB = r"""
const WRITES = [], ERRORS = [];
console.error = (...a) => ERRORS.push(a.map(String).join(" "));
function el() {
  const e = {
    style: {}, dataset: {}, value: "", textContent: "",
    classList: { add() {}, remove() {} },
    appendChild() {}, addEventListener() {}, closest() { return null; },
  };
  let html = "";
  Object.defineProperty(e, "innerHTML", {
    get: () => html, set: (v) => { html = v; WRITES.push(v); },
  });
  return e;
}
const ELEMENTS = {};
globalThis.document = {
  getElementById: (id) => (ELEMENTS[id] ||= el()),
  querySelector: (sel) => (ELEMENTS[sel] ||= el()),
  querySelectorAll: () => [],
  createElement: () => el(),
  addEventListener() {},
  documentElement: { clientWidth: 1000, clientHeight: 800 },
};
"""

DUMP = r"""
openDrill("category", DATA.rows[0].category);
openDrillRows("Внутри: " + DATA.rows[0].category, DATA.rows);
console.log(JSON.stringify({ writes: WRITES, errors: ERRORS }));
"""


def _write_csv(path: Path, rows: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(HEADERS)
        writer.writerows(rows)


@pytest.fixture
def build_from_rows(tmp_path, monkeypatch):
    """Run bd.build on made-up rows; return the output HTML text."""
    monkeypatch.setattr(bd, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setattr(bd, "DASHBOARD_DIR", tmp_path)
    monkeypatch.setattr(bd, "fetch_rates", lambda base: {"RSD": 1.0, "EUR": 0.0085})
    monkeypatch.setattr(bd, "get_chartjs", lambda: "/* chart.js stub */")

    def run(rows: list[list[str]], **build_kwargs: Any) -> str:
        csv_path = tmp_path / "data.csv"
        _write_csv(csv_path, rows)
        out = bd.build(base_iso="RSD", model="m", use_llm=False,
                       csv_path=str(csv_path), from_month=None, **build_kwargs)
        return out.read_text(encoding="utf-8")

    return run


def _page_script(html: str) -> str:
    """Return the dashboard script (the last <script> block)."""
    return re.findall(r"<script>(.*?)</script>", html, flags=re.S)[-1]


def test_script_close_in_data_keeps_page_intact(build_from_rows):
    comment = f"{SCRIPT_CLOSE} __CHARTJS__ a\u2028b"
    html = build_from_rows([["01.11.2026", "2026-11", "Food", "100", "дин", "Ann", comment]])

    # Exactly the two template scripts: Chart.js and the dashboard.
    assert html.lower().count("</script") == 2, "data closed or opened a script tag"
    script = _page_script(html)
    payload = re.search(r"const DATA = (.*);\n", script).group(1)
    data = json.loads(payload)
    assert data["rows"][0]["comment"] == comment


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node to run the page script")
def test_sheet_text_renders_as_text(build_from_rows, tmp_path):
    rows = [
        ["01.11.2026", "2026-11", XSS, "100", "дин", XSS, XSS],
        ["02.10.2026", "2026-10", XSS, "50", "€", "Bob", "кофе " + XSS],
    ]
    html = build_from_rows(rows)
    runner = tmp_path / "run.js"
    runner.write_text(DOM_STUB + _page_script(html) + DUMP, encoding="utf-8")
    proc = subprocess.run(["node", str(runner)], capture_output=True, text=True,
                          timeout=60, check=True)
    result = json.loads(proc.stdout)

    assert result["errors"] == []
    # Tooltips are stored URL-encoded in data-bd and decoded into innerHTML on hover.
    tooltips = [unquote(m) for w in result["writes"] for m in re.findall(r'data-bd="([^"]*)"', w)]
    rendered = result["writes"] + tooltips
    leaked = [w for w in rendered if "<img" in w]
    assert leaked == [], leaked[0][:300]
    assert any("&lt;img src=x onerror=alert(1)&gt;" in w for w in result["writes"])


def test_rows_parsed_by_shared_row(build_from_rows):
    # The build reads rows through sheet_data.parse_rows: dates come out as
    # DD.MM.YYYY, Currency words map through the registry, bad amounts are skipped.
    html = build_from_rows([
        ["2026-11-05", "", "Food", "1 200", "евро", "Ann", "обед"],
        ["06.11.2026", "", "Food", "не число", "дин", "Ann", "x"],
    ])
    data = json.loads(re.search(r"const DATA = (.*);\n", _page_script(html)).group(1))
    assert [(r["date"], r["month"], r["amount"], r["currency"]) for r in data["rows"]] == [
        ("05.11.2026", "2026-11", 1200.0, "EUR")]
