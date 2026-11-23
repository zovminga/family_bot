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
Restating Expense amounts in another Currency at the rate of the day the statistics are requested, not the day the Expense happened. The target is any Currency of the rate service, chosen with the same picker as manual input.
_Avoid_: Historical rate, exchange (as a noun)

**Large expense**:
An Expense marked as outside routine spending (flight tickets, a laptop), shown in the bot as «Крупная трата». It is stored in Data like any Expense but left out of routine statistics, and can still be looked at on request.
_Avoid_: One-off / разовая трата (that is a Preset section), outlier, anomaly

**Draft**:
An Expense proposed from voice, typed text («кофе 350») or receipt photo, shown for review before it is saved.
Each Draft card has its own short id; several can be open at once.
_Avoid_: Pending expense, suggestion

**Live card**:
One bot message that a step-by-step flow keeps editing instead of sending a new question per step (Draft edits; manual input next). The person's answers to it are deleted in a private chat; when newer bot messages bury it, it is sent again at the bottom.
_Avoid_: Chain of questions, prompt message

**Preset**:
A reusable Expense from the Regular_expenses sheet (monthly or one-off). A Preset may have no amount (utilities that change every month); the amount is then asked when it is used.
_Avoid_: Default expense, regular (as a noun), template

**Payment reminder**:
A message to the shared chat on the 5th and 20th listing the monthly Presets not yet entered this month, each with the Expenses of its Category already in Data this month, so a person decides whether it is still due.
_Avoid_: Notification, alert, overdue payment

**Sheets**:
The Google Spreadsheet that stores Data, Config, Meta, Regular_expenses, and Users worksheets.
_Avoid_: Database, workbook (when meaning this spreadsheet)

**Dashboard**:
A standalone HTML report of Expenses built offline and re-sent by the bot via a stored Telegram file_id.
_Avoid_: Stats page, report (when meaning the HTML artifact)

**Allowlist**:
Per-deploy map of Telegram user id → Spender name (`ALLOWED_USERS`). Strangers are denied.
_Avoid_: whitelist (say Allowlist), auth (when meaning this gate only)
