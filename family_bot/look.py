#!/usr/bin/env python3
"""Bot look, no Telegram: HTML text helpers and button styles.

Every bot message is sent with HTML parse mode (one Application setting), so
human text (comment, Category, receipt item, Preset name, Spender) must pass
``escape`` before it reaches a message. This is the only place that escapes.

Leaf helpers (``bold``, ``strike``, ``code``, ``field``) take plain text and
escape it. Block helpers (``quote``, ``expandable_quote``) take HTML already
built with the leaf helpers.

Button grids are rows of (label, callback_data) with an optional third item,
the style; the Telegram adapter (``navigation.inline_markup``) turns it into
the button's ``style`` field.
"""
from __future__ import annotations

import html as _html

# Cells of a share bar.
BAR_WIDTH = 10
BAR_FULL = "█"
BAR_EMPTY = "░"
# Russian forms of «трата» for 1, 2–4 and 5+ (and 11–14).
EXPENSE_FORMS = ("трата", "траты", "трат")

# Button styles (Bot API 9.4 ``style``); an older app shows a plain button.
SUCCESS = "success"
DANGER = "danger"
# (label, callback_data) or (label, callback_data, style).
Button = tuple[str, str] | tuple[str, str, str]
ButtonGrid = list[list[Button]]


def escape(text: str) -> str:
    """Plain text -> HTML: only ``<``, ``>``, ``&`` change; quotes stay as typed."""
    return _html.escape(text, quote=False)


def bold(text: str) -> str:
    return f"<b>{escape(text)}</b>"


def strike(text: str) -> str:
    return f"<s>{escape(text)}</s>"


def code(text: str) -> str:
    """Monospace; Telegram copies it with one tap."""
    return f"<code>{escape(text)}</code>"


def quote(html: str) -> str:
    """Blockquote around HTML (quotes cannot be nested)."""
    return f"<blockquote>{html}</blockquote>"


def expandable_quote(html: str) -> str:
    """Blockquote shown collapsed until tapped."""
    return f"<blockquote expandable>{html}</blockquote>"


def field(label: str, value: str) -> str:
    """«Сумма: <b>350 дин</b>»."""
    return f"{escape(label)}: {bold(value)}"


def bar(share: float, width: int = BAR_WIDTH) -> str:
    """
    Share as filled cells: 0.41 -> ``████░░░░░░``.

    Parameters
    ----------
    share : float
        Part of the total, 0..1; values outside are clamped.
    width : int
        Number of cells.

    Returns
    -------
    str
        ``width`` cells, the filled count rounded half up.
    """
    share = min(max(share, 0.0), 1.0)
    filled = int(share * width + 0.5)
    return BAR_FULL * filled + BAR_EMPTY * (width - filled)


def plural(n: int, forms: tuple[str, str, str]) -> str:
    """
    Russian word form for a count.

    Parameters
    ----------
    n : int
        Count.
    forms : tuple[str, str, str]
        Forms for 1, 2–4 and 5 («трата», «траты», «трат»).

    Returns
    -------
    str
        The form that goes after ``n``.
    """
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return forms[0]
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return forms[1]
    return forms[2]


def expenses(n: int) -> str:
    """«1 трата», «3 траты», «5 трат»."""
    return f"{n} {plural(n, EXPENSE_FORMS)}"
