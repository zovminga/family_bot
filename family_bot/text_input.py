#!/usr/bin/env python3
"""Typed expense input: «кофе 350» in the main menu → Draft.

Parsing reuses voice_input.extract_expenses (same Claude prompt, same snapping
to categories/currencies, same amount check), so text and voice cards match.
Telegram I/O stays in bot.py.
"""
from __future__ import annotations

import re
from typing import Optional

from family_bot import draft_expense as draft
from family_bot import voice_input

HINT = (
    "✍️ Чтобы записать трату, напишите товар и сумму, например: «кофе 350» "
    "или «такси 12 €». Или нажмите кнопку меню."
)

_DIGIT = re.compile(r"\d")


def looks_like_expense(text: Optional[str]) -> bool:
    """True when the text has a digit; anything else gets HINT without a Claude call."""
    return bool(_DIGIT.search(text or ""))


def draft_from_text(
    text: str,
    *,
    categories: list[str],
    currencies: list[str],
    today: str,
    who: str,
    default_currency: str,
) -> Optional[draft.DraftSession]:
    """
    Parse typed text into a Draft, or None when no expense was found.

    Parameters
    ----------
    text : str
        Message text, e.g. «кофе 350».
    categories, currencies : list[str]
        Allowed values the parsed items are snapped to.
    today : str
        Today as DD.MM.YYYY in the family time zone.
    who : str
        Spender name written into every item.
    default_currency : str
        Currency used when the text names none.

    Returns
    -------
    Optional[draft.DraftSession]
        Draft without an id (the caller adds it with draft.add_draft).
        Claude errors are raised to the caller.
    """
    result = voice_input.extract_expenses(
        text, categories, currencies, today, who, default_currency)
    items = result.get("expenses") or []
    if not items:
        return None
    return draft.session_from_text(items, who=who, text=text, note=result.get("note", ""))
