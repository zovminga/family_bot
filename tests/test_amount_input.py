"""Amount checks shared by manual input and voice/receipt Drafts."""
import pytest

import amount_input


@pytest.mark.parametrize("raw", ["nan", "inf", "-inf", "-500", "0", "0.00", "", "abc",
                                 float("nan"), float("inf"), -500, 0, None])
def test_positive_amount_rejects(raw):
    assert amount_input.positive_amount(raw) is None


@pytest.mark.parametrize("raw,expected", [
    ("350", 350.0), ("1 200", 1200.0), ("12,5", 12.5), ("1,234", 1234.0),
    (7, 7.0), (0.005, 0.01), (19.999, 20.0),
])
def test_positive_amount_accepts(raw, expected):
    assert amount_input.positive_amount(raw) == expected


@pytest.mark.parametrize("text,amount,symbol", [
    ("350", 350.0, None),
    ("1 200", 1200.0, None),
    ("1\u202f200", 1200.0, None),
    ("12.50", 12.5, None),
    ("350 дин", 350.0, "дин"),
    ("350дин", 350.0, "дин"),
    ("12,5 €", 12.5, "€"),
    ("€12.5", 12.5, "€"),
    ("$ 40", 40.0, "$"),
    ("1 200 rsd", 1200.0, "дин"),
    ("100 евро", 100.0, "€"),
    ("5000 рублей", 5000.0, "₽"),
])
def test_parse_amount_text_reads_amount_and_currency(text, amount, symbol):
    parsed = amount_input.parse_amount_text(text)
    assert parsed is not None, text
    got_amount, got_currency = parsed
    assert got_amount == amount
    assert (got_currency.symbol if got_currency else None) == symbol


@pytest.mark.parametrize("text", [
    "nan", "inf", "-500", "0", "", "abc", "350 xyz", "€350 дин", "1e400", "- 5",
    "1.234",  # three decimals: never silently rounded to 1.23
])
def test_parse_amount_text_rejects(text):
    assert amount_input.parse_amount_text(text) is None
