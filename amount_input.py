"""Amount checks for what a person types or says.

Number parsing is `sheet_data.parse_amount`, Currency lookup is the
`currency` registry; this module only adds the rules for new input: the amount
is a finite number above zero, and an optional Currency may stand before or
after it («350 дин», «€12,5»).
"""
from __future__ import annotations

import math
import re
from typing import Any, Optional

import currency
from sheet_data import parse_amount

# Optional Currency text, a number starting with a digit, optional Currency text.
_AMOUNT_TEXT = re.compile(r"^(?P<pre>[^\d\s+-]*)\s*(?P<num>\d[\d\s.,' ]*)(?P<post>.*)$")


def positive_amount(value: Any) -> Optional[float]:
    """Amount rounded to cents if it is a finite number above zero, else None."""
    amount = parse_amount(value)
    if amount is None or not math.isfinite(amount):
        return None
    amount = round(amount, 2)
    return amount if amount > 0 else None


def parse_amount_text(text: str) -> Optional[tuple[float, Optional[currency.Currency]]]:
    """Read «1 200», «350 дин», «€12,5» as (amount, Currency or None).

    None when the amount is not positive or the text around it is not a
    registered Currency.
    """
    match = _AMOUNT_TEXT.match((text or "").strip())
    if match is None:
        return None
    pre, post = match["pre"].strip(), match["post"].strip()
    if pre and post:
        return None
    amount = positive_amount(match["num"])
    if amount is None:
        return None
    cur_text = pre or post
    if not cur_text:
        return amount, None
    found = currency.find(cur_text)
    return (amount, found) if found is not None else None
