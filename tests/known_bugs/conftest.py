"""Fixtures for known-bug reproduction tests.

Import-time isolation from .env and Google Sheets lives in tests/conftest.py.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest


@pytest.fixture
def ctx() -> SimpleNamespace:
    return SimpleNamespace(user_data={}, args=[])
