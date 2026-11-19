#!/usr/bin/env python3
"""Tests for allowlist parsing and access checks."""
from __future__ import annotations

import pytest

from family_bot.auth import is_allowed, parse_allowed_users, spender_name


def test_parse_empty():
    assert parse_allowed_users(None) == {}
    assert parse_allowed_users("") == {}
    assert parse_allowed_users("  ") == {}


def test_parse_colon_and_equals():
    assert parse_allowed_users("111:Azat,222=Lisa") == {
        111: "Azat",
        222: "Lisa",
    }


def test_parse_whitespace_and_semicolon():
    assert parse_allowed_users(" 111 : Azat ; 222 : Lisa ") == {
        111: "Azat",
        222: "Lisa",
    }


def test_parse_invalid():
    with pytest.raises(ValueError, match="Invalid ALLOWED_USERS"):
        parse_allowed_users("Azat:111")


def test_is_allowed():
    users = {111: "Azat"}
    assert is_allowed(111, users) is True
    assert is_allowed(999, users) is False
    assert is_allowed(None, users) is False


def test_spender_name():
    users = {111: "Azat"}
    assert spender_name(111, "tg_name", users) == "Azat"
    assert spender_name(999, "tg_name", users) == "tg_name"


def test_parse_comma_inside_name():
    # I7: a comma inside a name must not split the entry
    assert parse_allowed_users("111:Иван, Петров,222:Lisa") == {
        111: "Иван, Петров",
        222: "Lisa",
    }


def test_parse_trailing_separator():
    assert parse_allowed_users("111:Azat,") == {111: "Azat"}
