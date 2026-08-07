# Family expenses

Telegram bot and offline dashboard for tracking shared household spending in a Google Sheet.

## Language

**Expense**:
A single spending row: date, category, amount, currency, who spent, and optional comment.
_Avoid_: Transaction, payment, entry (when meaning a saved spend)

**Category**:
A spending bucket loaded from the Config sheet (e.g. Food, Transport).
_Avoid_: Tag, type, label

**Spender**:
The person who paid (Lisa, Azat, or Both for shared presets).
_Avoid_: User (prefer Spender for sheet identity; User is the Telegram account)

**Draft**:
An Expense proposed from voice or receipt photo, shown for review before it is saved.
_Avoid_: Pending expense, suggestion

**Preset**:
A reusable Expense template from the Regular_expenses sheet (monthly or one-off).
_Avoid_: Default expense, regular (as a noun), template

**Sheets**:
The Google Spreadsheet that stores Data, Config, Meta, and Regular_expenses worksheets.
_Avoid_: Database, workbook (when meaning this spreadsheet)

**Dashboard**:
A standalone HTML report of Expenses built offline and re-sent by the bot via a stored Telegram file_id.
_Avoid_: Stats page, report (when meaning the HTML artifact)
