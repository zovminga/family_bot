"""Undo of the last save (issue #31, plan 3.3, F2)."""
from __future__ import annotations

from datetime import datetime, timedelta

from family_bot import undo
from family_bot.expense import ExpenseRecord
from tests.conftest import FakeSheetsClient

HEADERS = ["Date", "Month", "Category", "Amount", "Currency", "Who", "Comment"]
NOW = datetime(2026, 11, 19, 12, 0)
REC = ExpenseRecord(date="19.11.2026", category="Food", amount=350.0, currency="дин",
                    who="Azat", comment="")


def _client():
    return FakeSheetsClient(data_values=[
        HEADERS,
        ["19.11.2026", "2026-11", "Food", "350", "дин", "Azat", ""],
        ["19.11.2026", "2026-11", "Food", "90", "дин", "Lisa", "bread"],
    ])


def _callback(buttons):
    [[(label, data)]] = buttons
    assert label == "↩️ Отменить"
    return data


def test_undo_deletes_exactly_the_saved_row():
    user_data: dict = {}
    data = _callback(undo.remember(user_data, [REC], NOW))
    client = _client()
    result = undo.undo(user_data, data, client, NOW + timedelta(minutes=1))
    assert result.done
    assert result.text == "↩️ Отменено, запись удалена: Food — 350 дин, 19.11.2026"
    assert [r[6] for r in client.read_data_values()[1:]] == ["bread"]


def test_second_tap_deletes_nothing():
    user_data: dict = {}
    data = _callback(undo.remember(user_data, [REC], NOW))
    client = _client()
    undo.undo(user_data, data, client, NOW)
    client.append_data_row(["19.11.2026", "2026-11", "Food", 350.0, "дин", "Azat", ""])
    result = undo.undo(user_data, data, client, NOW)
    assert not result.done
    assert len(client.read_data_values()) == 3


def test_someone_elses_record_cannot_be_undone():
    owner: dict = {}
    data = _callback(undo.remember(owner, [REC], NOW))
    other: dict = {}  # user_data is per Telegram user
    client = _client()
    result = undo.undo(other, data, client, NOW)
    assert not result.done
    assert "только свою последнюю" in result.text
    assert len(client.read_data_values()) == 3


def test_an_earlier_save_cannot_be_undone():
    user_data: dict = {}
    first = _callback(undo.remember(user_data, [REC], NOW))
    undo.remember(user_data, [REC], NOW)
    result = undo.undo(user_data, first, _client(), NOW)
    assert not result.done
    assert "только свою последнюю" in result.text


def test_old_save_cannot_be_undone():
    user_data: dict = {}
    data = _callback(undo.remember(user_data, [REC], NOW))
    client = _client()
    result = undo.undo(user_data, data, client, NOW + undo.UNDO_WINDOW + timedelta(seconds=1))
    assert not result.done
    assert "30 минут" in result.text
    assert len(client.read_data_values()) == 3


def test_row_changed_in_sheet_is_not_deleted():
    user_data: dict = {}
    data = _callback(undo.remember(user_data, [REC], NOW))
    client = FakeSheetsClient(data_values=[
        HEADERS, ["19.11.2026", "2026-11", "Food", "360", "дин", "Azat", ""]])
    result = undo.undo(user_data, data, client, NOW)
    assert result.done  # final answer: the button goes away
    assert "изменена или удалена" in result.text
    assert len(client.read_data_values()) == 2


def test_several_rows_are_undone_together():
    user_data: dict = {}
    other = ExpenseRecord(date="19.11.2026", category="Food", amount=90, currency="дин",
                          who="Lisa", comment="bread")
    data = _callback(undo.remember(user_data, [REC, other], NOW))
    client = _client()
    result = undo.undo(user_data, data, client, NOW)
    assert result.text == "↩️ Отменено, удалено трат: 2"
    assert client.read_data_values() == [HEADERS]


def test_failed_delete_keeps_the_undo_available():
    class Broken(FakeSheetsClient):
        def delete_data_rows(self, row_numbers):
            raise RuntimeError("APIError: [503]")

    user_data: dict = {}
    data = _callback(undo.remember(user_data, [REC], NOW))
    try:
        undo.undo(user_data, data, Broken(data_values=_client().read_data_values()), NOW)
    except RuntimeError:
        pass
    assert undo.undo(user_data, data, _client(), NOW).done
