"""Known-bug reproductions for data integrity and the dashboard builder.

Each test asserts the correct behaviour and is marked xfail(strict=True) with
its finding id from docs/specs/2026-11-revision.md. A fix removes the marker.
"""
import sys
from pathlib import Path

import pytest
import requests

from tests.known_bugs.fakes import FakeSheets, known_bug

DASHBOARD_DIR = Path(__file__).resolve().parents[2] / "dashboard"
if str(DASHBOARD_DIR) not in sys.path:
    sys.path.insert(0, str(DASHBOARD_DIR))

import build_dashboard as bd  # noqa: E402
from expense import ExpenseRecord, append_expense  # noqa: E402
from sheet_data import normalize_rows, parse_amount  # noqa: E402
import voice_input as vx  # noqa: E402


# 13. Thousands separator read as decimal: "1,234" -> 1.234, "12,500" -> 12.5.
@pytest.mark.parametrize("raw,expected", [("1,234", 1234.0), ("12,500", 12500.0)])
def test_parse_amount_thousands_comma(raw, expected):
    assert parse_amount(raw) == expected


# 14. Reads resolve columns by header, writes use fixed order → moved header = corrupted rows.
def test_append_respects_header_order():
    headers = ["Date", "Category", "Month", "Amount", "Currency", "Who", "Comment"]
    sheets = FakeSheets(headers=headers)
    append_expense(sheets, ExpenseRecord(date="01.11.2026", category="Food",
                                         amount=250.0, currency="дин", who="Azat"))
    rec = normalize_rows(headers, sheets.rows)[0]
    assert rec["category"] == "Food" and rec["month"] == "2026-11", rec


# 15. Voice path keeps negative / zero amounts (photo path drops them).
@known_bug("R6")
@pytest.mark.parametrize("raw", [-500, 0])
def test_voice_amount_must_be_positive(raw):
    assert vx._snap_amount(raw) is None


# 16. A transient LLM failure must not be cached as "Прочее" — the next run retries it.
def test_failed_llm_batch_is_retried_next_run(tmp_path, monkeypatch):
    monkeypatch.setattr(bd, "OUTPUT_DIR", tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")

    def broken(prompt, model):
        raise TimeoutError("overloaded")

    def working(prompt, model):
        return {"Такси домой": "Такси"}

    monkeypatch.setitem(bd.LLM_PROVIDERS, "anthropic", ("ANTHROPIC_API_KEY", broken))
    bd.group_comments_llm(["Такси домой"], "anthropic", "m")
    monkeypatch.setitem(bd.LLM_PROVIDERS, "anthropic", ("ANTHROPIC_API_KEY", working))
    cache = bd.group_comments_llm(["Такси домой"], "anthropic", "m")
    assert cache["Такси домой"] == "Такси", cache


# 17. Publish error text must not contain the bot token (it goes to build.log / launchd.log via tee).
def test_publish_error_does_not_leak_token(tmp_path, monkeypatch):
    token = "123456:SECRET-TOKEN"
    monkeypatch.setenv("BOT_TOKEN", token)
    monkeypatch.setenv("DASHBOARD_CHAT_ID", "1")
    html = tmp_path / "d.html"
    html.write_text("<html></html>")

    def fake_post(url, **kw):
        r = requests.Response()
        r.status_code = 400
        r.reason = "Bad Request"
        r.url = url
        return r

    monkeypatch.setattr(bd.requests, "post", fake_post)
    with pytest.raises(requests.HTTPError) as exc:
        bd.publish_to_telegram(html)
    assert token not in str(exc.value), str(exc.value)


# 18. Currency missing from FX table → amount_base None → template treats it as 0.
# The build must stop and name the offending rows instead.
def test_unknown_currency_stops_build(tmp_path, monkeypatch):
    monkeypatch.setattr(bd, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setattr(bd, "DASHBOARD_DIR", tmp_path)
    monkeypatch.setattr(bd, "fetch_rates", lambda base: {"RSD": 1.0, "EUR": 0.0085})
    monkeypatch.setattr(bd, "get_chartjs", lambda: "")
    csv_path = tmp_path / "data.csv"
    csv_path.write_text(
        "Date,Category,Amount,Currency,Who,Comment\n"
        "01.11.2026,Food,100,дин,Azat,ok\n"
        "02.11.2026,Food,1000,руб,Azat,Пятёрочка\n"
        "03.11.2026,Food,7,XYZ,Lisa,mystery\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError) as exc:
        bd.build(base_iso="RSD", model="m", use_llm=False,
                 csv_path=str(csv_path), from_month=None)
    msg = str(exc.value)
    assert "02.11.2026" in msg and "руб" in msg and "Пятёрочка" in msg, msg
    assert "03.11.2026" in msg and "XYZ" in msg, msg
    assert "01.11.2026" not in msg, msg
    assert not (tmp_path / "output" / "expenses_dashboard.html").exists()
