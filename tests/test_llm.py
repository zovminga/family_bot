"""llm: Claude client options and JSON extraction from model replies."""
from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any

import pytest

from family_bot import llm


@pytest.mark.parametrize("reply", [
    '{"a": 1}',
    '```json\n{"a": 1}\n```',
    '```\n{"a": 1}\n```',
    'Вот ответ:\n{"a": 1}\nГотово.',
    '  {"a": 1}  ',
])
def test_extract_json_object(reply: str) -> None:
    assert llm.extract_json_object(reply) == {"a": 1}


def test_extract_json_object_rejects_non_json() -> None:
    with pytest.raises(ValueError):
        llm.extract_json_object("не JSON")


def test_reply_text_joins_text_blocks() -> None:
    resp = SimpleNamespace(content=[
        SimpleNamespace(type="text", text='{"a":'),
        SimpleNamespace(type="tool_use"),
        SimpleNamespace(type="text", text=" 1}"),
    ])
    assert llm.reply_text(resp) == '{"a": 1}'


def test_client_has_short_timeout_and_one_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}
    monkeypatch.setitem(sys.modules, "anthropic",
                        SimpleNamespace(Anthropic=lambda **kw: seen.update(kw)))
    llm.client()
    assert seen == {"timeout": 30.0, "max_retries": 1}
