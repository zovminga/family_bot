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
from typing import Any, Optional, Protocol, runtime_checkable

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

    return gspread.service_account(filename=str(creds_file), scopes=SCOPE).open(name)


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
        """Append one Data-sheet row (built by ExpenseRecord.to_row).

        RAW stores values as given: a comment starting with "=" stays text and
        "2026-11" is not turned into a date.
        """
        self._worksheet(DATA_SHEET).append_row(row, value_input_option="RAW")

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

        return changed


def open_client(
    creds_path: Optional[str] = None,
    sheet_name: Optional[str] = None,
) -> GspreadSheetsClient:
    """Factory used by bot, regular, and dashboard."""
    return GspreadSheetsClient.from_env(creds_path=creds_path, sheet_name=sheet_name)
