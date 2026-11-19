#!/usr/bin/env python3
"""Conversation state constants for the Telegram expense bot.

One ConversationHandler wires three flows (manual add, preset, stats) plus the
main menu. Keeping the ints here makes each flow's diagram readable in isolation.
"""
from __future__ import annotations

(
    CHOOSE_ACTION,
    # Manual add-expense flow
    CHOOSE_CAT,
    TYPING_AMT,
    CHOOSE_CUR,
    TYPING_CMNT,
    CHOOSE_DT,
    TYPING_DT,
    # Preset (⚡) flow — not shared with CHOOSE_CAT
    CHOOSE_PRESET,
    # Statistics flow
    STAT_CAT,
    STAT_TYPE,
    STAT_DATE_FROM,
    STAT_DATE_TO,
    STAT_MONTH,
    STAT_GROUP_CURRENCY,
    STAT_CONVERT_CURRENCY,
    STAT_SHOW_DETAILS,
) = range(16)
