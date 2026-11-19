"""Fixtures for known-bug reproduction tests.

Isolation from .env and Google Sheets lives in tests/conftest.py.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.conftest import bot_context


@pytest.fixture
def ctx() -> SimpleNamespace:
    return bot_context()
