#!/usr/bin/env python3
"""Tests for draft confirmation transitions (no Telegram)."""
from __future__ import annotations

from datetime import datetime

from family_bot.draft_expense import (
    CALLBACK_DATA_MAX_BYTES,
    MAX_CLOSED_DRAFTS,
    DraftSession,
    add_draft,
    apply_callback,
    category_buttons,
    card_text,
    get_draft,
    main_buttons,
    parse_callback,
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
    session = DraftSession(id="ab12", items=[_item(), _item(comment="tea")])
    rows = main_buttons(session)
    assert rows[0][0][1] == "vx:ab12:save"
    assert any(cb.startswith("vx:ab12:ed:") for _, cb in rows[1])


def test_parse_callback():
    assert parse_callback("vx:ab12:setcat:0:Food: out") == ("ab12", "setcat:0:Food: out")
    assert parse_callback("vx:save") == ("", "")  # card from before draft ids
    assert parse_callback("other:ab12:save") == ("", "")


def test_set_category_and_save():
    cats = ["Food", "Transport"]
    curs = ["дин", "€"]
    client = FakeSheetsClient()
    session = DraftSession(id="ab12", items=[_item(category="Food")])

    outcome = apply_callback("setcat:0:Transport", session, cats, curs, client)
    assert session.items[0]["category"] == "Transport"
    assert outcome.buttons is not None
    assert "Transport" in outcome.text

    outcome = apply_callback("save", session, cats, curs, client)
    assert session.closed
    assert "Сохранено" in outcome.text
    assert len(client.appended_rows) == 1
    assert client.appended_rows[0][2] == "Transport"


def test_save_writes_all_items_in_one_append_call():
    calls: list[list[list]] = []

    class OneCallClient(FakeSheetsClient):
        def append_data_rows(self, rows):
            calls.append(rows)

    session = DraftSession(id="ab12", items=[_item(), _item(comment="tea")])
    apply_callback("save", session, ["Food"], ["дин"], OneCallClient())
    assert [[r[6] for r in rows] for rows in calls] == [["lunch", "tea"]]


def test_failed_save_keeps_draft_open():
    class BrokenClient(FakeSheetsClient):
        def append_data_rows(self, rows):
            raise RuntimeError("APIError")

    session = DraftSession(id="ab12", items=[_item()])
    try:
        apply_callback("save", session, ["Food"], ["дин"], BrokenClient())
    except RuntimeError:
        pass
    assert not session.closed


def test_draft_is_closed_while_save_runs():
    # Save runs in a worker thread (issue #18): a tap arriving mid-write must see a closed Draft.
    seen = []

    class WatchingClient(FakeSheetsClient):
        def append_data_rows(self, rows):
            seen.append(session.closed)
            super().append_data_rows(rows)

    session = DraftSession(id="ab12", items=[_item()])
    apply_callback("save", session, ["Food"], ["дин"], WatchingClient())
    assert seen == [True] and session.closed


def test_long_category_name_survives_reorder():
    long_name = "Развлечения, подписки и прочие радости жизни"
    session = DraftSession(id="ab12", items=[_item()])
    rows = category_buttons(session, 0, ["Food", long_name])
    label, cb = rows[0][1]
    assert label == long_name
    assert len(cb.encode()) <= CALLBACK_DATA_MAX_BYTES
    _, payload = parse_callback(cb)
    reordered = [long_name, "Other", "Food"]  # Config re-sorted, then /reloadcats
    apply_callback(payload, session, reordered, ["дин"], FakeSheetsClient())
    assert session.items[0]["category"] == long_name


def test_long_category_removed_from_config_changes_nothing():
    long_name = "Развлечения, подписки и прочие радости жизни"
    session = DraftSession(id="ab12", items=[_item(category="Food")])
    _, cb = category_buttons(session, 0, [long_name])[0][0]
    _, payload = parse_callback(cb)
    apply_callback(payload, session, ["Food"], ["дин"], FakeSheetsClient())
    assert session.items[0]["category"] == "Food"


def test_setdate_yesterday():
    cats = ["Food"]
    curs = ["дин"]
    client = FakeSheetsClient()
    session = DraftSession(items=[_item()])
    now = datetime(2026, 8, 7, 12, 0, 0)

    outcome = apply_callback("setdate:0:y", session, cats, curs, client, now=now)
    assert session.items[0]["date"] == "06.08.2026"
    assert outcome.text is not None


def test_cancel_closes_and_later_taps_do_nothing():
    client = FakeSheetsClient()
    session = DraftSession(items=[_item()], header="x")
    outcome = apply_callback("cancel", session, ["Food"], ["дин"], client)
    assert session.closed
    assert outcome.text == "❌ Отменено."
    assert apply_callback("save", session, ["Food"], ["дин"], client).text is None
    assert client.appended_rows == []


def test_add_draft_gives_distinct_ids_and_keeps_both():
    user_data: dict = {}
    a = add_draft(user_data, session_from_voice([_item()], who="Azat", transcript="a"))
    b = add_draft(user_data, session_from_voice([_item()], who="Azat", transcript="b"))
    assert a.id and b.id and a.id != b.id
    assert get_draft(user_data, a.id) is a
    assert get_draft(user_data, b.id) is b
    assert get_draft(user_data, "nope") is None


def test_add_draft_prunes_old_closed_drafts_only():
    user_data: dict = {}
    first_open = add_draft(user_data, DraftSession(items=[_item()]))
    for _ in range(30):
        add_draft(user_data, DraftSession(items=[_item()], closed=True))
    add_draft(user_data, DraftSession(items=[_item()]))
    drafts = user_data["drafts"]
    assert first_open.id in drafts
    assert sum(d.closed for d in drafts.values()) == MAX_CLOSED_DRAFTS
