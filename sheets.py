#!/usr/bin/env python3
"""Google Sheets access for family expense data.

One SheetsClient module sits at the seam between the bot / presets / dashboard
and gspread. Callers use domain methods (categories, data rows, Meta file_id);
they do not authorize or open worksheets themselves.
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable

BOT_DIR = Path(__file__).resolve().parent

SCOPE = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]

DATA_SHEET = "Data"
CONFIG_SHEET = "Config"
META_SHEET = "Meta"
REGULAR_SHEET = "Regular_expenses"
META_FILE_ID_CELL = "B1"
META_FILE_ID_LABEL_CELL = "A1"
META_UPDATED_AT_LABEL_CELL = "A2"
META_UPDATED_AT_CELL = "B2"


def resolve_creds_path(creds_path: Optional[str] = None) -> Path:
    """Resolve GOOGLE_CREDS_PATH, allowing paths relative to the bot directory."""
    raw = creds_path if creds_path is not None else os.getenv("GOOGLE_CREDS_PATH")
    if not raw:
        raise RuntimeError("Environment variable GOOGLE_CREDS_PATH not found.")
    path = Path(raw)
    if not path.is_absolute():
        path = (BOT_DIR / path).resolve()
    return path


def _authorize_spreadsheet(
    creds_path: Optional[str] = None,
    sheet_name: Optional[str] = None,
):
    """Open the configured spreadsheet via gspread (production adapter)."""
    import gspread
    from oauth2client.service_account import ServiceAccountCredentials

    creds_file = resolve_creds_path(creds_path)
    if not creds_file.exists():
        raise FileNotFoundError(f"Service account file not found: {creds_file}")

    name = sheet_name if sheet_name is not None else os.getenv("SHEET_NAME")
    if not name:
        raise RuntimeError("Environment variable SHEET_NAME not found.")

    creds = ServiceAccountCredentials.from_json_keyfile_name(str(creds_file), SCOPE)
    return gspread.authorize(creds).open(name)


@runtime_checkable
class SheetsClient(Protocol):
    """Domain interface for spreadsheet access.

    Production adapter: GspreadSheetsClient.
    Tests: any object satisfying these methods (e.g. FakeSheetsClient).
    """

    @property
    def url(self) -> Optional[str]: ...

    def test_connection(self) -> bool: ...

    def read_categories(self) -> list[str]: ...

    def read_data_values(self) -> list[list[str]]: ...

    def read_data_records(self) -> list[dict]: ...

    def read_data_headers(self) -> list[str]: ...

    def append_data_row(self, row: list) -> None: ...

    def read_regular_values(self) -> list[list[str]]: ...

    def get_dashboard_file_id(self) -> Optional[str]: ...

    def set_dashboard_file_id(self, file_id: str) -> None: ...


class GspreadSheetsClient:
    """gspread adapter implementing SheetsClient."""

    def __init__(self, spreadsheet):
        self._spreadsheet = spreadsheet

    @classmethod
    def from_env(
        cls,
        creds_path: Optional[str] = None,
        sheet_name: Optional[str] = None,
    ) -> "GspreadSheetsClient":
        return cls(_authorize_spreadsheet(creds_path=creds_path, sheet_name=sheet_name))

    @property
    def url(self) -> Optional[str]:
        return getattr(self._spreadsheet, "url", None)

    def _worksheet(self, name: str):
        return self._spreadsheet.worksheet(name)

    def test_connection(self) -> bool:
        try:
            self._worksheet(CONFIG_SHEET)
            return True
        except Exception as e:  # noqa: BLE001 - surface as False to callers
            print(f"❌ Google Sheets connection error: {e}")
            return False

    def read_categories(self) -> list[str]:
        """Read category names from column A of Config (skip header)."""
        col = self._worksheet(CONFIG_SHEET).col_values(1)
        col = [c.strip() for c in col if c.strip()]
        return col[1:] if len(col) > 1 else []

    def read_data_values(self) -> list[list[str]]:
        return self._worksheet(DATA_SHEET).get_all_values()

    def read_data_records(self) -> list[dict]:
        return self._worksheet(DATA_SHEET).get_all_records()

    def read_data_headers(self) -> list[str]:
        return self._worksheet(DATA_SHEET).row_values(1) or []

    def append_data_row(self, row: list) -> None:
        """Append one raw Data-sheet row (used via ExpenseRecord.to_row)."""
        self._worksheet(DATA_SHEET).append_row(row, value_input_option="USER_ENTERED")

    def read_regular_values(self) -> list[list[str]]:
        return self._worksheet(REGULAR_SHEET).get_all_values()

    def get_dashboard_file_id(self) -> Optional[str]:
        value = self._worksheet(META_SHEET).acell(META_FILE_ID_CELL).value
        return value.strip() if value else None

    def set_dashboard_file_id(self, file_id: str) -> None:
        """Write dashboard Telegram file_id + timestamp into Meta (create sheet if needed)."""
        import gspread

        try:
            ws = self._worksheet(META_SHEET)
        except gspread.WorksheetNotFound:
            ws = self._spreadsheet.add_worksheet(title=META_SHEET, rows=10, cols=2)
        ws.update_acell(META_FILE_ID_LABEL_CELL, "dashboard_file_id")
        ws.update_acell(META_FILE_ID_CELL, file_id)
        ws.update_acell(META_UPDATED_AT_LABEL_CELL, "updated_at")
        ws.update_acell(
            META_UPDATED_AT_CELL,
            datetime.now().strftime("%Y-%m-%d %H:%M"),
        )


def open_client(
    creds_path: Optional[str] = None,
    sheet_name: Optional[str] = None,
) -> GspreadSheetsClient:
    """Factory used by bot, regular, and dashboard."""
    return GspreadSheetsClient.from_env(creds_path=creds_path, sheet_name=sheet_name)
