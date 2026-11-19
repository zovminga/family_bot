#!/usr/bin/env python3
"""Google Sheets access for family expense data.

One SheetsClient module sits at the seam between the bot / presets / dashboard
and gspread. Callers use domain methods (categories, data rows, Meta file_id);
they do not authorize or open worksheets themselves.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional, Protocol, runtime_checkable

from family_bot import clock

# Repo root (family_bot/ lives one level below): relative GOOGLE_CREDS_PATH
# such as secrets/creds.json resolves from here.
BOT_DIR = Path(__file__).resolve().parent.parent

SCOPE = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]

DATA_SHEET = "Data"
CONFIG_SHEET = "Config"
META_SHEET = "Meta"
REGULAR_SHEET = "Regular_expenses"
USERS_SHEET = "Users"
META_FILE_ID_CELL = "B1"
META_FILE_ID_LABEL_CELL = "A1"
META_UPDATED_AT_LABEL_CELL = "A2"
META_UPDATED_AT_CELL = "B2"

# Default layout for a fresh family spreadsheet (bootstrap / first install).
DATA_HEADERS = ["Date", "Month", "Category", "Amount", "Currency", "Who", "Comment"]
CONFIG_HEADER = "Categories"
DEFAULT_CATEGORIES = [
    "Food",
    "Transport",
    "Entertainment",
    "Rent",
    "Utilities",
    "Health",
    "Other",
]
REGULAR_HEADERS = [
    "название",
    "динары",
    "евро",
    "доллары",
    "рубли",
    "категория",
    "кто",
]
# Users: one row per Telegram account. Recent currencies are stored values,
# newest first, joined by RECENT_SEPARATOR («CNY, дин, €»).
USERS_HEADERS = ["User ID", "Name", "Recent currencies"]
USERS_ID_COL, USERS_NAME_COL, USERS_RECENT_COL = range(3)
RECENT_SEPARATOR = ", "
USERS_ROWS = 20  # initial size; Sheets grows it on append
REGULAR_SECTION_MONTHLY = "ежемесячные траты"
REGULAR_SECTION_ONEOFF = "разовые траты"


def resolve_creds_path(creds_path: Optional[str] = None) -> Path:
    """Resolve GOOGLE_CREDS_PATH, allowing paths relative to the bot directory."""
    raw = creds_path if creds_path is not None else os.getenv("GOOGLE_CREDS_PATH")
    if not raw:
        raise RuntimeError("Environment variable GOOGLE_CREDS_PATH not found.")
    path = Path(raw)
    if not path.is_absolute():
        path = (BOT_DIR / path).resolve()
    return path


class SpreadsheetNotFound(RuntimeError):
    """SHEET_NAME names no spreadsheet the service account can open."""


def _authorize_spreadsheet(
    creds_path: Optional[str] = None,
    sheet_name: Optional[str] = None,
):
    """Open the configured spreadsheet via gspread (production adapter)."""
    import gspread

    creds_file = resolve_creds_path(creds_path)
    if not creds_file.exists():
        raise FileNotFoundError(f"Service account file not found: {creds_file}")

    name = sheet_name if sheet_name is not None else os.getenv("SHEET_NAME")
    if not name:
        raise RuntimeError("Environment variable SHEET_NAME not found.")

    try:
        return gspread.service_account(filename=str(creds_file), scopes=SCOPE).open(name)
    except gspread.SpreadsheetNotFound as e:
        raise SpreadsheetNotFound(name) from e


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

    def append_data_rows(self, rows: list[list]) -> None: ...

    def delete_data_rows(self, row_numbers: list[int]) -> None: ...

    def read_regular_values(self) -> list[list[str]]: ...

    def get_dashboard_file_id(self) -> Optional[str]: ...

    def set_dashboard_file_id(self, file_id: str) -> None: ...

    def read_recent_currencies(self, user_id: int) -> Optional[list[str]]: ...

    def write_recent_currencies(self, user_id: int, name: str, values: list[str]) -> None: ...


def _users_row(values: list[list[str]], user_id: int) -> Optional[int]:
    """1-based row number of user_id in Users values (header in row 1), else None."""
    key = str(user_id)
    for n, row in enumerate(values[1:], start=2):
        if row and row[USERS_ID_COL].strip() == key:
            return n
    return None


def _recent_cell(values: list[list[str]], row_number: int) -> list[str]:
    row = values[row_number - 1]
    cell = row[USERS_RECENT_COL] if len(row) > USERS_RECENT_COL else ""
    return [v.strip() for v in cell.split(RECENT_SEPARATOR.strip()) if v.strip()]


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
        """Append one Data-sheet row (built by ExpenseRecord.to_row).

        RAW stores values as given: a comment starting with "=" stays text and
        "2026-11" is not turned into a date.
        """
        self._worksheet(DATA_SHEET).append_row(row, value_input_option="RAW")

    def append_data_rows(self, rows: list[list]) -> None:
        """Append several Data rows in one API request (all land or none do)."""
        self._worksheet(DATA_SHEET).append_rows(rows, value_input_option="RAW")

    def delete_data_rows(self, row_numbers: list[int]) -> None:
        """Delete Data rows by 1-based sheet row number in one API request.

        Rows go bottom-up, so deleting one does not shift the next.
        """
        ws = self._worksheet(DATA_SHEET)
        requests = [
            {"deleteDimension": {"range": {
                "sheetId": ws.id, "dimension": "ROWS", "startIndex": n - 1, "endIndex": n,
            }}}
            for n in sorted(set(row_numbers), reverse=True)
        ]
        self._spreadsheet.batch_update({"requests": requests})

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
            clock.now().strftime("%Y-%m-%d %H:%M"),
        )

    def read_recent_currencies(self, user_id: int) -> Optional[list[str]]:
        """Recent currencies of a User from Users; None if no row (or no sheet)."""
        import gspread

        try:
            values = self._worksheet(USERS_SHEET).get_all_values()
        except gspread.WorksheetNotFound:
            return None
        n = _users_row(values, user_id)
        return (_recent_cell(values, n) or None) if n is not None else None

    def write_recent_currencies(self, user_id: int, name: str, values: list[str]) -> None:
        """Save a User's Recent currencies: update their Users row or add one.

        Creates the Users sheet with its headers when missing. RAW keeps
        «€, дин» and the id as plain text.
        """
        from gspread.utils import rowcol_to_a1

        ws, _ = self._ensure_worksheet(USERS_SHEET, rows=USERS_ROWS, cols=len(USERS_HEADERS))
        rows = ws.get_all_values()
        if not rows or not any(c.strip() for c in rows[0]):
            ws.update(range_name="A1", values=[USERS_HEADERS], value_input_option="RAW")
            rows = [USERS_HEADERS]
        text = RECENT_SEPARATOR.join(values)
        n = _users_row(rows, user_id)
        if n is None:
            ws.append_row([str(user_id), name, text], value_input_option="RAW")
            return
        ws.update(range_name=rowcol_to_a1(n, USERS_RECENT_COL + 1), values=[[text]],
                  value_input_option="RAW")

    def _ensure_worksheet(
        self, title: str, rows: int = 100, cols: int = 10
    ) -> tuple[Any, bool]:
        """Return (worksheet, created). Creates the worksheet if missing."""
        import gspread

        try:
            return self._worksheet(title), False
        except gspread.WorksheetNotFound:
            return (
                self._spreadsheet.add_worksheet(title=title, rows=rows, cols=cols),
                True,
            )

    def ensure_sheets(
        self,
        *,
        categories: Optional[list[str]] = None,
        seed_if_empty: bool = True,
    ) -> dict[str, bool]:
        """Create missing worksheets and seed default headers/categories if empty.

        Safe to re-run: never overwrites non-empty Data / Config / Regular sheets.
        Returns which sheets were created or seeded (for installer feedback).
        """
        cats = categories if categories is not None else list(DEFAULT_CATEGORIES)
        changed = {
            "data_created": False,
            "data_headers": False,
            "config_created": False,
            "config_seeded": False,
            "regular_created": False,
            "regular_seeded": False,
            "meta_created": False,
            "meta_seeded": False,
            "users_created": False,
            "users_seeded": False,
        }

        data_ws, changed["data_created"] = self._ensure_worksheet(
            DATA_SHEET, rows=500, cols=len(DATA_HEADERS)
        )
        if seed_if_empty and not any(c.strip() for c in (data_ws.row_values(1) or [])):
            data_ws.update(range_name="A1", values=[DATA_HEADERS])
            changed["data_headers"] = True

        config_ws, changed["config_created"] = self._ensure_worksheet(
            CONFIG_SHEET, rows=50, cols=1
        )
        if seed_if_empty and not any(c.strip() for c in (config_ws.col_values(1) or [])):
            config_ws.update(
                range_name="A1",
                values=[[CONFIG_HEADER], *[[c] for c in cats]],
            )
            changed["config_seeded"] = True

        regular_ws, changed["regular_created"] = self._ensure_worksheet(
            REGULAR_SHEET, rows=50, cols=len(REGULAR_HEADERS)
        )
        if seed_if_empty and not any(
            c.strip() for c in (regular_ws.row_values(1) or [])
        ):
            regular_ws.update(
                range_name="A1",
                values=[
                    REGULAR_HEADERS,
                    [REGULAR_SECTION_MONTHLY, "", "", "", "", "", ""],
                    [REGULAR_SECTION_ONEOFF, "", "", "", "", "", ""],
                ],
            )
            changed["regular_seeded"] = True

        meta_ws, changed["meta_created"] = self._ensure_worksheet(
            META_SHEET, rows=10, cols=2
        )
        if seed_if_empty and not (
            meta_ws.acell(META_FILE_ID_LABEL_CELL).value or ""
        ).strip():
            meta_ws.update_acell(META_FILE_ID_LABEL_CELL, "dashboard_file_id")
            meta_ws.update_acell(META_UPDATED_AT_LABEL_CELL, "updated_at")
            changed["meta_seeded"] = True

        users_ws, changed["users_created"] = self._ensure_worksheet(
            USERS_SHEET, rows=USERS_ROWS, cols=len(USERS_HEADERS)
        )
        if seed_if_empty and not any(c.strip() for c in (users_ws.row_values(1) or [])):
            users_ws.update(range_name="A1", values=[USERS_HEADERS])
            changed["users_seeded"] = True

        return changed


def open_client(
    creds_path: Optional[str] = None,
    sheet_name: Optional[str] = None,
) -> GspreadSheetsClient:
    """Factory used by bot, regular, and dashboard."""
    return GspreadSheetsClient.from_env(creds_path=creds_path, sheet_name=sheet_name)
