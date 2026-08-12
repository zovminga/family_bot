#!/usr/bin/env python3
"""Per-deployment allowlist: who may use this bot instance.

Each self-hosted install has its own BOT_TOKEN, Google Sheet, and ALLOWED_USERS.
Family members on one deploy share one sheet; strangers who find the bot are denied.
"""
from __future__ import annotations

import os
import re
from typing import Optional

# "123456:Azat,789012:Lisa" — commas or whitespace between pairs; : or = between id and name.
_PAIR = re.compile(
    r"^\s*(?P<uid>\d+)\s*[:=]\s*(?P<name>[^,;]+?)\s*$"
)


def parse_allowed_users(raw: Optional[str]) -> dict[int, str]:
    """Parse ALLOWED_USERS env value into {telegram_id: spender_name}."""
    if not raw or not raw.strip():
        return {}
    users: dict[int, str] = {}
    for chunk in re.split(r"[,;]+", raw):
        chunk = chunk.strip()
        if not chunk:
            continue
        match = _PAIR.match(chunk)
        if not match:
            raise ValueError(
                f"Invalid ALLOWED_USERS entry {chunk!r}. "
                'Expected form: 123456:Name (e.g. "111:Azat,222:Lisa")'
            )
        users[int(match.group("uid"))] = match.group("name").strip()
    return users


def load_allowed_users() -> dict[int, str]:
    """Load allowlist from ALLOWED_USERS in the environment."""
    return parse_allowed_users(os.getenv("ALLOWED_USERS"))


def is_allowed(user_id: Optional[int], allowed: dict[int, str]) -> bool:
    if user_id is None:
        return False
    return user_id in allowed


def spender_name(
    user_id: Optional[int],
    fallback: str,
    allowed: dict[int, str],
) -> str:
    """Sheet 'Who' label: mapped name if allowlisted, else Telegram display name."""
    if user_id is not None and user_id in allowed:
        return allowed[user_id]
    return fallback
