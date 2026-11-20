#!/usr/bin/env python3
"""Conversation states for the Telegram expense bot.

One ConversationHandler wires three flows (manual add, preset, stats) plus the
main menu. Keeping the states here makes each flow's diagram readable in isolation.
"""
from __future__ import annotations

from enum import IntEnum, auto


class State(IntEnum):
    """ConversationHandler states; tests check each one has a handler."""

    CHOOSE_ACTION = 0
    # Manual add-expense flow (flows/manual.py)
    CHOOSE_CAT = auto()
    TYPING_AMT = auto()
    CHOOSE_CUR = auto()
    ASK_LARGE = auto()
    TYPING_CMNT = auto()
    CHOOSE_DT = auto()
    TYPING_DT = auto()
    # Preset (⚡) flow — not shared with CHOOSE_CAT
    CHOOSE_PRESET = auto()
    TYPING_PRESET_AMT = auto()  # Preset without an amount asks it
    # Statistics flow (flows/stats.py)
    STAT_CAT = auto()
    STAT_TYPE = auto()
    STAT_DATE_FROM = auto()
    STAT_DATE_TO = auto()
    STAT_MONTH = auto()
    STAT_GROUP_CURRENCY = auto()
    STAT_CONVERT_CURRENCY = auto()
    STAT_SHOW_DETAILS = auto()
