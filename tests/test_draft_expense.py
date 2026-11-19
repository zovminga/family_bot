#!/usr/bin/env python3
"""Tests for draft confirmation transitions (no Telegram)."""
from __future__ import annotations

from datetime import datetime

from draft_expense import (
    DraftSession,
    apply_callback,
    card_text,
    main_buttons,
    session_from_receipt,
    session_from_voice,
)
from tests.conftest import FakeSheetsClient


def _item(**overrides):
    base = {
        "date": "07.08.2026",
        "category": "Food",
        "amount": 12.5,
        "currency": "дин",
        "who": "Azat",
        "comment": "lunch",
    }
    base.update(overrides)
    return base


def test_card_text_single_voice():
    session = session_from_voice([_item()], who="Azat", transcript="ланч двенадцать")
    text = card_text(session)
    assert "Расшифровка" in text
    assert "12.50" in text or "12,50" in text
    assert "Food" in text


def test_card_text_receipt_header():
    session = session_from_receipt(
        [_item(), _item(comment="beer", amount=3.0, category="Alcohol")],
        who="Lisa",
        merchant="Maxi",
    )
    text = card_text(session)
    assert "Чек · Maxi" in text
    assert "2 трат" in text


def test_main_buttons_multi_item_edit_rows():
    session = DraftSession(items=[_item(), _item(comment="tea")])
    rows = main_buttons(session)
    assert rows[0][0][1] == "vx:save"
    assert any(cb.startswith("vx:ed:") for _, cb in rows[1])


def test_set_category_and_save(monkeypatch):
    cats = ["Food", "Transport"]
    curs = ["дин", "€"]
    client = FakeSheetsClient()
    session = DraftSession(items=[_item(category="Food")])

    outcome = apply_callback("setcat:0:1", session, cats, curs, client)
    assert session.items[0]["category"] == "Transport"
    assert outcome.buttons is not None
    assert "Transport" in outcome.text

    outcome = apply_callback("save", session, cats, curs, client)
    assert outcome.clear_session
    assert "Сохранено" in outcome.text
    assert len(client.appended_rows) == 1
    assert client.appended_rows[0][2] == "Transport"


def test_setdate_yesterday():
    cats = ["Food"]
    curs = ["дин"]
    client = FakeSheetsClient()
    session = DraftSession(items=[_item()])
    now = datetime(2026, 8, 7, 12, 0, 0)

    outcome = apply_callback("setdate:0:y", session, cats, curs, client, now=now)
    assert session.items[0]["date"] == "06.08.2026"
    assert outcome.text is not None


def test_cancel_clears():
    client = FakeSheetsClient()
    session = DraftSession(items=[_item()], header="x")
    outcome = apply_callback("cancel", session, ["Food"], ["дин"], client)
    assert outcome.clear_session
    assert outcome.text == "❌ Отменено."


def test_session_store_and_clear():
    user_data: dict = {}
    session = session_from_voice([_item()], who="Azat", transcript="hi", note="n")
    session.store(user_data)
    assert user_data["vx_items"][0]["who"] == "Azat"
    assert "vx_header" not in user_data

    loaded = DraftSession.load(user_data)
    assert loaded.transcript == "hi"
    DraftSession.clear(user_data)
    assert "vx_items" not in user_data
