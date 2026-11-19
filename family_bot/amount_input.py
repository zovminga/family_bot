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

from family_bot import currency
from family_bot.sheet_data import parse_amount

# Groups: Currency text before the number, the number (starts with a digit,
# may hold spaces, dots, commas, apostrophes), Currency text after it.
_AMOUNT_WITH_CURRENCY = re.compile(r"^(?P<pre>[^\d\s+-]*)\s*(?P<num>\d[\d\s.,' ]*)(?P<post>.*)$")


def positive_amount(value: Any) -> Optional[float]:
    """
    Check an amount from voice, a receipt or a typed message.

    Parameters
    ----------
    value : Any
        Number or text accepted by `sheet_data.parse_amount`.

    Returns
    -------
    float or None
        The amount rounded to cents if it is finite and above zero, else None.
    """
    amount = parse_amount(value)
    if amount is None or not math.isfinite(amount):
        return None
    amount = round(amount, 2)
    return amount if amount > 0 else None


def parse_amount_text(text: str) -> Optional[tuple[float, Optional[currency.Currency]]]:
    """
    Read a typed amount with an optional Currency: «1 200», «350 дин», «€12,5».

    Parameters
    ----------
    text : str
        Message text.

    Returns
    -------
    tuple of (float, Currency or None), or None
        None when the amount is not above zero, has more than two decimals
        («1.234» is not rounded to 1.23), or the text around it is not a
        registered Currency.
    """
    match = _AMOUNT_WITH_CURRENCY.match((text or "").strip())
    if match is None:
        return None
    pre, post = match["pre"].strip(), match["post"].strip()
    if pre and post:
        return None
    raw = parse_amount(match["num"])
    amount = positive_amount(raw)
    if amount is None or amount != raw:
        return None
    cur_text = pre or post
    if not cur_text:
        return amount, None
    found = currency.find(cur_text)
    return (amount, found) if found is not None else None
