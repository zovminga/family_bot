# Family expenses — domain language

Telegram bot and offline dashboard for tracking shared household spending in a Google Sheet.

Agents: start at [`AGENTS.md`](AGENTS.md). Prefer these terms in UI copy, code names, commits, and docs.

## Language

**Expense**:
A single spending row: date, category, amount, currency, who spent, and optional comment.
_Avoid_: Transaction, payment, entry (when meaning a saved spend)

**Category**:
A spending bucket loaded from the Config sheet (e.g. Food, Transport).
_Avoid_: Tag, type, label

**Spender**:
The person who paid (a household member's name, or Both for shared presets).
_Avoid_: User (prefer Spender for sheet identity; User is the Telegram account)

**Currency**:
The money unit of an Expense, written as a symbol for the long-standing ones (дин, €, ₽, ¥, £, $) or as an ISO 4217 code for any other (CNY, GEL). `¥` always means Japanese yen; Chinese yuan is `CNY`.
_Avoid_: `¥` for yuan, "base currency" (when meaning the Currency of one Expense)

**Recent currencies**:
The three Currencies a User entered most recently, offered first when choosing a Currency. Kept per Telegram account in the `Users` sheet; a new User starts with the three most frequent in Data.
_Avoid_: Default currency, favourite currencies

**Conversion**:
Restating Expense amounts in another Currency at the rate of the day the statistics are requested, not the day the Expense happened.
_Avoid_: Historical rate, exchange (as a noun)

**Draft**:
An Expense proposed from voice, typed text («кофе 350») or receipt photo, shown for review before it is saved.
Each Draft card has its own short id; several can be open at once.
_Avoid_: Pending expense, suggestion

**Preset**:
A reusable Expense from the Regular_expenses sheet (monthly or one-off).
_Avoid_: Default expense, regular (as a noun), template

**Sheets**:
The Google Spreadsheet that stores Data, Config, Meta, Regular_expenses, and Users worksheets.
_Avoid_: Database, workbook (when meaning this spreadsheet)

**Dashboard**:
A standalone HTML report of Expenses built offline and re-sent by the bot via a stored Telegram file_id.
_Avoid_: Stats page, report (when meaning the HTML artifact)

**Allowlist**:
Per-deploy map of Telegram user id → Spender name (`ALLOWED_USERS`). Strangers are denied.
_Avoid_: whitelist (say Allowlist), auth (when meaning this gate only)
