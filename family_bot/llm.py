"""Claude client settings and JSON extraction from model replies (bot and dashboard)."""
from __future__ import annotations

import json
from typing import Any

# Claude client limits for bot calls: a hung request fails fast instead of holding the user.
LLM_TIMEOUT_SEC = 30.0
LLM_MAX_RETRIES = 1


def client() -> Any:
    """
    Anthropic client for bot calls (reads ANTHROPIC_API_KEY from the environment).

    Returns
    -------
    anthropic.Anthropic
        Client with ``LLM_TIMEOUT_SEC`` and ``LLM_MAX_RETRIES``.
    """
    import anthropic

    return anthropic.Anthropic(timeout=LLM_TIMEOUT_SEC, max_retries=LLM_MAX_RETRIES)


def reply_text(resp: Any) -> str:
    """
    Text of a Messages API reply: its text blocks joined.

    Parameters
    ----------
    resp : Any
        ``client.messages.create(...)`` result.

    Returns
    -------
    str
        Concatenated text of all ``type == "text"`` blocks.
    """
    return "".join(block.text for block in resp.content if block.type == "text")


def extract_json_object(text: str) -> dict:
    """
    Parse a JSON object from a model reply, tolerating ```json fences and prose around it.

    Parameters
    ----------
    text : str
        Model reply text.

    Returns
    -------
    dict
        The parsed object.

    Raises
    ------
    ValueError
        No JSON object could be parsed (``json.JSONDecodeError`` is a ``ValueError``).
    """
    text = text.strip()
    if text.startswith("```"):
        # Strip a leading ```json / ``` fence and the trailing ```.
        text = text.split("\n", 1)[-1] if "\n" in text else text
        text = text.rsplit("```", 1)[0].strip()
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start:end + 1]
    return json.loads(text)
