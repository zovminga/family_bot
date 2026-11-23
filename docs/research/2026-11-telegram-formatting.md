# Telegram message formatting and "modern look" features

Research for the idea: make the bot look nicer and more modern (formatting, buttons, effects,
Mini App). Access date for all sources: 2026-11-23. Current Bot API at that date: **10.5**
(2026-11-22) [S2].

Labels: **[verified]** = read in a primary source or in the installed library code; **[hypothesis]** =
not verified.

## Summary

- **Repo state [verified].** `requirements.txt` pins `python-telegram-bot==22.8`; `pyproject.toml`
  allows `>=22.2,<23`. PTB 22.8 (2026-06-12) is the latest release and supports **Bot API 10.0**
  (`telegram.__bot_api_version__ == "10.0"`) [S9, S10]. No PTB release covers Bot API 10.1–10.5.
  The bot uses legacy `parse_mode="Markdown"` in 2 places (`family_bot/bot.py`, allowlist notice);
  the other ~76 `reply_text` / `send_message` / `edit_message_text` calls send plain text.
- **Switch to HTML parse mode (high confidence).** HTML needs only `<`, `>`, `&` escaped (`html.escape`)
  and supports everything legacy Markdown lacks: underline, strikethrough, spoiler, block quote,
  expandable block quote, custom emoji, date/time. Parsing happens on Telegram's side, so new **tags**
  need no PTB upgrade. Set it once with `Defaults(parse_mode=ParseMode.HTML)`; then every existing
  plain-text message that contains `<` or `&` must be escaped, or it fails to send.
- **Rich Messages (Bot API 10.1, June 2026) are the biggest "modern look" change.** A separate method
  `sendRichMessage` takes Markdown/HTML with headings, lists, **tables**, collapsible blocks, footnotes,
  formulas, media and inline buttons, up to 32768 characters [S1 #rich-messages]. A monthly spending
  report could be a real table. PTB 22.8 has no native support → call it via
  `bot.do_api_request("sendRichMessage", ...)` [S13]. Rendering on older Telegram clients was not checked.
- **Coloured buttons and button icons (Bot API 9.4, PTB 22.7+) [verified].** `style` =
  `"primary"` (blue) / `"success"` (green) / `"danger"` (red) on `InlineKeyboardButton` and
  `KeyboardButton`. Works for every bot, no restriction stated. `icon_custom_emoji_id` on buttons
  and `<tg-emoji>` in text work only if the bot bought a Fragment username **or** the bot owner has
  Telegram Premium.
- **Streaming (Bot API 9.3/9.5, PTB 22.6+).** `sendMessageDraft` shows a live 30-second draft in
  **private chats only**; useful while Claude reads a receipt photo. Message effects
  (`message_effect_id`) are also **private chats only**.
- **Mini App.** Ways to open: menu button, `web_app` inline/reply button (private chats only), main
  Mini App and direct `t.me` links (also from groups). PTB's built-in webhook server serves **one**
  route only, so serving a Mini App page from the same Render service needs the "custom webhook"
  setup (Starlette + uvicorn feeding `application.update_queue`) [S11, S12].

## 1. HTML parse mode

### 1a. Supported tags today (Bot API 10.5) [verified, S1 #html-style]

| Tag | Entity | Since |
|---|---|---|
| `<b>`, `<strong>` | bold | early Bot API (parse_mode HTML exists since 2016 [S2 #january-20-2016]) |
| `<i>`, `<em>` | italic | early Bot API |
| `<u>`, `<ins>` | underline | 4.5 [S2 #december-31-2019] |
| `<s>`, `<strike>`, `<del>` | strikethrough | 4.5 |
| `<span class="tg-spoiler">`, `<tg-spoiler>` | spoiler | 5.6 [S2 #december-30-2021] |
| `<a href="http://…">` | text_link | early Bot API |
| `<a href="tg://user?id=123">` | text_mention | early Bot API |
| `<tg-emoji emoji-id="…">😀</tg-emoji>` | custom_emoji | 6.7 (Fragment bots) [S2 #april-21-2023]; 9.4 (Premium owner) [S2 #february-9-2026] |
| `<tg-time unix="…" format="wDT">text</tg-time>` | date_time | 9.5 [S2 #march-1-2026] (entity type; HTML tag shown in the current docs) |
| `<code>` | code | early Bot API |
| `<pre>`, `<pre><code class="language-python">` | pre (+language) | early Bot API; version of the `language` attribute not pinned down |
| `<blockquote>` | blockquote | 7.0 [S2 #december-29-2023] |
| `<blockquote expandable>` | expandable_blockquote | 7.4 [S2 #may-28-2024] |

"Only the tags mentioned above are currently supported." No `<br>`, `<p>`, headings, lists or tables
in ordinary messages: line breaks are plain `\n` [S1 #html-style]. Those exist only in Rich Messages
(section 3a).

`tg-time` format string must match `r|w?[dD]?[tT]?`: `r` relative time, `w` weekday, `d`/`D` short/long
date, `t`/`T` short/long time; empty format shows the text as-is [S1 #date-time-entity-formatting].
Useful for "payment due …" reminders: the user sees the date in their own locale.

### 1b. Escaping [verified, S1 #html-style]

- All `<`, `>`, `&` that are not part of a tag or entity must be replaced with `&lt;`, `&gt;`, `&amp;`.
- All numerical entities are supported. Named entities: only `&lt;`, `&gt;`, `&amp;`, `&quot;`.
- Python's `html.escape(s)` escapes `& < > " '` (`'` → `&#x27;`, a numerical entity), so its output is
  valid. PTB itself uses `html.escape` in `telegram.helpers.mention_html` [S10 `helpers.py`].
- `<tg-emoji>` content must be a valid ordinary emoji: it is shown where custom emoji can't be
  (notifications, forwards by non-Premium users).

### 1c. Nesting [verified, S1 #formatting-options]

- If two entities overlap, one must fully contain the other.
- bold, italic, underline, strikethrough, spoiler can contain and be inside any entity, except `pre`
  and `code`.
- blockquote and expandable_blockquote can't be nested.
- All other entities can't contain each other.
- `tg://user?id=` links work only inside an inline link or an inline button, and are guaranteed only
  if the user is in the chat or has contacted the bot in private.

### 1d. Limits [verified]

- `sendMessage.text`: **1–4096 characters after entities parsing** [S1 #sendmessage]. Tags do not count.
- Media captions: 0–1024 characters after entities parsing (e.g. `sendAnimation`, `sendAudio`) [S1].
- Entity offsets/lengths are in UTF-16 code units [S1 #messageentity].
- **Entity count limit: not documented** in the Bot API. Not verified.

### 1e. HTML vs MarkdownV2 vs legacy Markdown [verified, S1]

| | HTML | MarkdownV2 | Markdown (legacy) |
|---|---|---|---|
| Must escape | `<` `>` `&` | `_ * [ ] ( ) ~ \` > # + - = \| { } . !` everywhere outside entities; inside `pre`/`code`: `` ` `` and `\`; inside link `(...)`: `)` and `\` | `_ * \` [` outside entities; no escaping inside entities (close and reopen instead) |
| Nesting | yes | yes | **no** |
| underline, strikethrough, spoiler, blockquote, expandable blockquote, custom emoji, date_time | yes | yes | **no** — "use parse mode MarkdownV2 instead" |
| Status | current | current | "legacy mode, retained for backward compatibility" |

MarkdownV2 also has a `__`/`_` ambiguity rule (`___italic underline_**__`). For a bot that inserts
user text (comments, category names, amounts with `.` and `-`), HTML has the smallest escape set:
every `.` and `-` in an amount like `-12.50` must be escaped in MarkdownV2.

## 2. Custom emoji and button styles

**[verified, S1 #html-style, #inlinekeyboardbutton]** Custom emoji entities "can only be used by bots
that purchased additional usernames on Fragment or in the messages directly sent by the bot to
private, group and supergroup chats if the owner of the bot has a Telegram Premium subscription."
The Premium-owner path was added in Bot API 9.4 (2026-02-09) [S2 #february-9-2026]. So an ordinary
bot can use them only if its owner has Premium. Without that, the bot must use ordinary emoji.

Buttons (both `InlineKeyboardButton` and `KeyboardButton`), Bot API 9.4 [S1, S2 #february-9-2026]:

| Field | Values | Restriction |
|---|---|---|
| `icon_custom_emoji_id` | custom emoji id; shown before the button text | same Fragment / Premium-owner rule as above |
| `style` | `"danger"` (red), `"success"` (green), `"primary"` (blue); omitted = app default | none stated |

PTB: `style` and `icon_custom_emoji_id` exist in 22.7+, with enum `telegram.constants.KeyboardButtonStyle`
(`PRIMARY`, `SUCCESS`, `DANGER`) [S10 `constants.py`, `_inline/inlinekeyboardbutton.py`]. The
docstring notes: "`style` option will only work in Telegram versions released after February 9, 2026."
Since `pyproject.toml` allows 22.2, raise the lower bound to 22.7 if `style` is used.

Other button fields relevant to looks [verified, S1 #inlinekeyboardbutton]:

- `copy_text` (Bot API 7.11) — button copies text to the clipboard.
- `disabled` / `DisabledButton` (Bot API 10.3) — a button that does nothing. **Not in PTB 22.8.**
- Mini App button `web_app` — private chats only (section 4).
- In Rich Messages, `<tg-button>` also accepts `style="link"` (only seen in the doc example, not in
  the field list) [S1 #rich-html-style].

Getting a custom emoji id: `getCustomEmojiStickers` returns sticker info [S1 #messageentity]. How to
find ids of existing public packs was not researched.

## 3. Other "modern look" features

### 3a. Rich Messages — Bot API 10.1–10.5 [verified, S1 #rich-messages, S2]

- `sendRichMessage(chat_id, rich_message=InputRichMessage, reply_markup, message_effect_id, …)`;
  `InputRichMessage` takes exactly one of `html`, `markdown` or `blocks`.
- Rich HTML adds: `<h1>`–`<h6>`, `<p>`, `<ul>/<ol>/<li>`, checkbox list items, `<table>` (with
  `bordered striped compact`, `colspan`, `align`), `<details>/<summary>` (collapsible), `<footer>`,
  `<hr/>`, `<mark>`, `<sub>/<sup>`, footnotes (`<tg-reference>`), `<tg-math>`, media blocks, maps,
  collages, inline buttons inside the text (`<tg-button>`, `<tg-button-row>`). Rich Markdown is
  "compatible with GitHub Flavored Markdown where possible" and can embed the HTML tags.
- Named entities in rich HTML: also `&apos; &nbsp; &hellip; &mdash; &ndash;` and curly quotes.
- Limits: 32768 UTF-8 characters, 500 blocks, 16 nesting levels, 50 media, 20 table columns.
- `editMessageText` accepts `rich_message` (10.1). `sendRichMessageDraft` streams a rich draft (10.1).
- No private-chat restriction is stated for `sendRichMessage` (only `message_effect_id` is private-only).
- Not in PTB 22.8. Use `await bot.do_api_request("sendRichMessage", api_kwargs={...}, return_type=Message)`
  per the PTB forward-compatibility wiki [S13]. The incoming `Message.rich_message` field will be in
  `message.api_kwargs`.
- **[hypothesis]** How older Telegram clients show a rich message (fallback text or "unsupported
  message") is not documented on the pages read.

### 3b. Link previews — Bot API 7.0 [verified, S1 #linkpreviewoptions, S2 #december-29-2023]

`LinkPreviewOptions`: `is_disabled`, `url`, `prefer_small_media`, `prefer_large_media`,
`show_above_text`. Replaced `disable_web_page_preview`. PTB 20.8+ (also settable in `Defaults`).

### 3c. Message effects — Bot API 7.4 [verified, S1 #sendmessage, S2 #may-28-2024]

`message_effect_id`: "for private chats only" (`sendMessage`, media methods, `sendRichMessage`;
forward/copy since 9.3). **The Bot API does not list valid effect ids.** How to obtain them was not
verified.

### 3d. Replies and quotes — Bot API 7.0 [verified, S1 #replyparameters]

`reply_parameters`: `message_id`, `chat_id`, `quote` (0–1024 chars, must be an exact substring of
the original message, else the send fails), `quote_parse_mode`, `quote_entities`, `quote_position`;
later `checklist_task_id` (9.2), `poll_option_id` (9.6), `ephemeral_message_id` (10.2),
`quote_rich_texts` (10.4).

### 3e. Streaming partial messages [verified, S1 #sendmessagedraft, S2]

`sendMessageDraft(chat_id, draft_id, text, parse_mode, …)`: target is a **private chat**; draft is a
30-second preview; the bot must then call `sendMessage` with the final text. Added in 9.3, opened
to all bots in 9.5, empty text (shows "Thinking…") in 10.0, `can_stop` / `keep_on_stop` in 10.3.
PTB 22.6+ has `Bot.send_message_draft`; `can_stop` / `keep_on_stop` are not in 22.8 (pass via
`api_kwargs`).

### 3f. Checklists — Bot API 9.1 [verified, S1 #sendchecklist]

`sendChecklist` / `editMessageChecklist` send a checklist **only on behalf of a connected business
account** (`business_connection_id` required). Not usable by a normal household bot. Rich messages
have static checkbox list items (`<input type="checkbox" checked>`); whether users can tick them
was not verified.

### 3g. Other recent entries (brief) [verified, S2]

- **Ephemeral messages** (10.2–10.4): in groups, a message visible to one user only, e.g. an
  answer to a button press (`ephemeral_message_parameters` with `callback_query_id`). Could hide
  per-user confirmations in a family group chat.
- **Widgets** (10.4): `sendWidget` sends an HTML widget (≤32768 bytes) inside a message; "in the
  production environment, widgets can be sent only to private chats" and are always protected
  content.
- **Telegram Serverless** (Mini App hosting at `app<id>.tgcloud.ai`, JavaScript modules only)
  [S8]. Not applicable to this Python bot without a rewrite; listed for completeness.

## 4. Telegram Mini Apps

### 4a. Ways to open [verified, S3 #implementing-mini-apps]

| Way | How | Groups? |
|---|---|---|
| Menu button | BotFather `/setmenubutton` or `setChatMenuButton` (`MenuButtonWebApp`) | `setChatMenuButton` targets private chats [S1 #setchatmenubutton] |
| Reply keyboard `web_app` button | `KeyboardButton(web_app=WebAppInfo(url))`; app can `sendData` back | **Private chats only** [S1 #keyboardbutton] |
| Inline keyboard `web_app` button | `InlineKeyboardButton(web_app=…)`; gets user info + `query_id` | **Private chats only** [S1 #inlinekeyboardbutton] |
| Main Mini App | BotFather; profile "Launch app" button; `https://t.me/<bot>?startapp` | Link opens "in the current chat"; `chat_type`/`chat_instance` passed (Bot API 7.8) |
| Direct link | `https://t.me/<bot>/<appname>?startapp=…` | Any chat; no access to chat messages |
| Inline mode | `answerInlineQuery(button=…)` | any chat via inline mode |
| Attachment menu | only major advertisers in production | — |

For a family group chat, a `t.me` link (main or direct Mini App) is the way to open it from the group;
`web_app` buttons work only in the private chat with the bot. `WebAppInitData` is empty when opened
from a reply-keyboard button [S3 #webappinitdata].

### 4b. Theme [verified, S3 #themeparams]

`ThemeParams` fields, each also a CSS variable: `--tg-theme-bg-color`, `-text-color`, `-hint-color`,
`-link-color`, `-button-color`, `-button-text-color`, `-secondary-bg-color` (6.1+),
`-header-bg-color`, `-accent-text-color`, `-section-bg-color`, `-section-header-text-color`,
`-subtitle-text-color`, `-destructive-text-color` (7.0+), `-section-separator-color` (7.6+),
`-bottom-bar-bg-color` (7.10+). Plus `--tg-color-scheme`, `--tg-viewport-height`,
`--tg-viewport-stable-height`. Load `https://telegram.org/js/telegram-web-app.js` in `<head>`.

### 4c. initData validation [verified, S3 #validating-data-received-via-the-mini-app]

1. Frontend sends the raw `Telegram.WebApp.initData` query string to the backend.
2. `data_check_string` = all fields except `hash`, sorted by key, `key=value` joined with `\n`.
3. `secret_key = HMAC_SHA256(key="WebAppData", msg=bot_token)`.
4. Valid if `hex(HMAC_SHA256(key=secret_key, msg=data_check_string)) == hash`. Also check `auth_date`
   for freshness.

A third party without the token can check the Ed25519 `signature` field with Telegram's public key
(production key `e7bf03a2…242d`) [S3 #validating-data-for-third-party-use]. PTB has no helper for
this check (no match in the installed package) [S10]; it is ~15 lines with `hmac` + `hashlib`.
Since Bot API 10.2, Mini App methods are blocked from origins other than the Mini App's domain by
default (from 2026-07-20) [S2 #july-14-2026].

### 4d. Hosting on the same Render web service [verified, S10, S11, S12]

- `Application.run_webhook` starts PTB's Tornado server whose route table is exactly one handler:
  `handlers = [(rf"{webhook_path}/?", TelegramHandler, ...)]`
  (`telegram/ext/_utils/webhookhandler.py`). No static files, no extra endpoints.
- The PTB wiki "Webhooks → Custom solution": skip `Updater`, run your own web server and put decoded
  updates into `application.update_queue` (`Update.de_json(...)`). Official examples:
  `examples/customwebhookbot/{starlettebot,flaskbot,quartbot,djangobot}.py`. The Starlette one
  routes `/telegram`, `/healthcheck`, `/submitpayload` under uvicorn.
- So: Mini App page + initData-checked API on the same service = replace `run_webhook` with a
  Starlette (or similar ASGI) app. The webhook secret check (`secret_token` header) then has to be
  done in our own route **[hypothesis: the Starlette example was not read line by line for a
  secret check]**.
- Alternative: host the static page elsewhere (any HTTPS URL) and keep `run_webhook`. Any data
  endpoint still needs a server that validates initData.

## 5. python-telegram-bot

- **Latest release: 22.8, 2026-06-12** (PyPI and GitHub releases) [S9]. README of tag v22.8:
  "All types and methods of the Telegram Bot API **10.0** are natively supported." [S9]
  Changelog: 22.6 → Bot API 9.3; 22.7 → 9.4 and 9.5; 22.8 → 9.6 and 10.0 [S11].
  Master branch (commits up to 2026-11-22) has no Bot API 10.1+ support merged; community PRs for
  10.3 and rich messages were closed [S9]. **Upgrade for Bot API 10.1+: not available** — use
  `api_kwargs` / `do_api_request` [S13].
- **`ParseMode.HTML`** = `"HTML"` in `telegram.constants.ParseMode`; also `MARKDOWN_V2`, and
  `MARKDOWN` with the note "You should use `MARKDOWN_V2` instead" [S10 `constants.py`].
- **`telegram.helpers.escape_markdown(text, version=1, entity_type=None)`**: v1 escapes `` _*`[ ``;
  v2 escapes `\_*[]()~`>#+-=|{}.!`, or only `` \` `` inside `pre`/`code`, or only `\)` inside
  `text_link`/`custom_emoji` [S10 `helpers.py`]. For HTML there is no PTB helper: use `html.escape`.
- **`Defaults(parse_mode=ParseMode.HTML)`** passed via `Application.builder().defaults(defaults)`.
  Caveats [S10 `ext/_defaults.py`, S12 "Adding defaults to your bot"]:
  - Immutable after creation.
  - Applies to `parse_mode`, `explanation_parse_mode`, `text_parse_mode`, `question_parse_mode`.
  - Override per call with `parse_mode=None` (plain text) or another mode.
  - Defaults work through `ExtBot` (the bot built by `Application`); a bare `telegram.Bot` ignores them.
  - Side effect for this repo: all current plain-text messages become HTML-parsed. Any user-supplied
    text (comments, category names, receipt item names from Claude Vision) containing `<` or `&` will
    then fail with "can't parse entities" unless escaped.
  - Also settable: `link_preview_options`, `do_quote`, `tzinfo`, `disable_notification`,
    `protect_content`, `allow_sending_without_reply`, `block`.
  - `api_kwargs` calls (e.g. `sendRichMessage`) do not use `parse_mode` anyway (`InputRichMessage.html`).

## Feature table

PTB column = first PTB version with native support. "Repo (22.8)" = available with the pinned version.

| Feature | Bot API | PTB | Repo (22.8) | Works in groups |
|---|---|---|---|---|
| HTML: b/i/code/pre/a | ≤2.x | any | yes | yes |
| HTML: u, s (+nesting) | 4.5 | server-side parse, any | yes | yes |
| HTML: spoiler | 5.6 | server-side, any | yes | yes |
| HTML: blockquote | 7.0 | server-side, any | yes | yes |
| HTML: expandable blockquote | 7.4 | server-side, any (enum 21.3) | yes | yes |
| `<tg-emoji>` custom emoji | 6.7 / 9.4 | server-side, any | yes, if owner has Premium or Fragment username | yes (private, group, supergroup) |
| `<tg-time>` date_time | 9.5 | server-side; enum 22.7 | yes | yes |
| `link_preview_options` | 7.0 | 20.8 | yes | yes |
| `reply_parameters` / quote | 7.0 | 20.8 | yes | yes |
| `message_effect_id` | 7.4 | 21.3 | yes | **no — private only** |
| `copy_text` button | 7.11 | 21.7 | yes | yes |
| Button `style` (colour) | 9.4 | 22.7 | yes | yes (no restriction stated) |
| Button `icon_custom_emoji_id` | 9.4 | 22.7 | yes, Premium/Fragment rule | yes |
| `sendMessageDraft` | 9.3 (all bots 9.5) | 22.6 | yes | **no — private only** |
| `DisabledButton` | 10.3 | — | via `api_kwargs` | yes (assumed, not stated) |
| Rich Messages (`sendRichMessage`) | 10.1–10.5 | — | via `do_api_request` | yes (no restriction stated) |
| Ephemeral messages | 10.2–10.4 | — | via `api_kwargs` | groups only make sense |
| Widgets (`sendWidget`) | 10.4 | — | via `do_api_request` | **no — private only (production)** |
| Checklists (`sendChecklist`) | 9.1 | 22.3 | yes, but business accounts only | n/a |
| Mini App `web_app` keyboard / inline button | 6.0 | 20.0 | yes | **no — private only** |
| Mini App menu button | 6.0 | 20.0 | yes | private chats |
| Main Mini App / direct link | 7.8 / 6.x | link, no API | yes | yes (via `t.me` link) |

## Open questions

1. How Rich Messages look on older Telegram clients (fallback or "unsupported message").
2. Valid `message_effect_id` values: not listed in the Bot API.
3. Whether the family uses the bot in a group chat or only in private chats (decides effects,
   drafts, `web_app` buttons). Not checked in the code beyond the allowlist.
4. Does the bot owner have Telegram Premium (gate for custom emoji in text and on buttons)?
5. Whether checkbox items in Rich Messages can be ticked by users.
6. Exact Bot API version of `<pre><code class="language-…">` and of the `<tg-time>` HTML tag.
7. Entity count limit per message: not documented.
8. When PTB adds Bot API 10.1+ (no release or merged PR found on 2026-11-23).

## Sources

All primary, accessed 2026-11-23.

- [S1] Telegram Bot API — https://core.telegram.org/bots/api ; anchors used: `#formatting-options`,
  `#html-style`, `#markdownv2-style`, `#markdown-style`, `#date-time-entity-formatting`, `#sendmessage`,
  `#messageentity`, `#inlinekeyboardbutton`, `#keyboardbutton`, `#linkpreviewoptions`,
  `#replyparameters`, `#sendmessagedraft`, `#sendchecklist`, `#inputchecklist`, `#disabledbutton`,
  `#setchatmenubutton`, `#menubuttonwebapp`, `#rich-messages`, `#rich-message-limits`,
  `#rich-markdown-style`, `#rich-html-style`, `#sendrichmessage`, `#inputrichmessage`,
  `#sendrichmessagedraft`, `#sendwidget`, `#ephemeral-messages-and-commands`
- [S2] Bot API changelog — https://core.telegram.org/bots/api-changelog ; anchors
  `#november-22-2026` (10.5), `#november-9-2026` (10.4), `#august-24-2026` (10.3), `#july-14-2026` (10.2),
  `#june-11-2026` (10.1), `#may-8-2026` (10.0), `#march-1-2026` (9.5), `#february-9-2026` (9.4),
  `#december-31-2025` (9.3), `#july-3-2025` (9.1), `#october-31-2024` (7.11), `#july-31-2024` (7.8),
  `#may-28-2024` (7.4), `#december-29-2023` (7.0), `#april-21-2023` (6.7), `#april-16-2022` (6.0),
  `#december-30-2021` (5.6), `#december-31-2019` (4.5), `#january-20-2016`
- [S3] Telegram Mini Apps — https://core.telegram.org/bots/webapps ; anchors `#implementing-mini-apps`,
  `#keyboard-button-mini-apps`, `#inline-button-mini-apps`, `#launching-mini-apps-from-the-menu-button`,
  `#launching-the-main-mini-app`, `#direct-link-mini-apps`, `#themeparams`, `#webappinitdata`,
  `#validating-data-received-via-the-mini-app`, `#validating-data-for-third-party-use`
- [S8] Telegram Serverless — https://core.telegram.org/bots/serverless
- [S9] python-telegram-bot releases — https://github.com/python-telegram-bot/python-telegram-bot/releases ;
  PyPI — https://pypi.org/pypi/python-telegram-bot/json ; README at tags v22.6–v22.8
  (`gh api repos/python-telegram-bot/python-telegram-bot/contents/README.rst?ref=v22.8`); recent commits
  and PRs via `gh`
- [S10] Installed PTB 22.8 source in `.venv` (`telegram/constants.py`, `telegram/helpers.py`,
  `telegram/ext/_defaults.py`, `telegram/_inline/inlinekeyboardbutton.py`,
  `telegram/ext/_utils/webhookhandler.py`, `telegram/_bot.py`)
- [S11] PTB changelog — https://docs.python-telegram-bot.org/en/v22.8/changelog.html ; custom webhook
  examples — https://docs.python-telegram-bot.org/en/stable/examples.customwebhookbot.html and
  https://github.com/python-telegram-bot/python-telegram-bot/tree/master/examples/customwebhookbot ;
  `examples/webappbot.py`
- [S12] PTB wiki — https://github.com/python-telegram-bot/python-telegram-bot/wiki/Adding-defaults-to-your-bot ,
  https://github.com/python-telegram-bot/python-telegram-bot/wiki/Webhooks (section "Custom solution")
- [S13] PTB wiki — https://github.com/python-telegram-bot/python-telegram-bot/wiki/Bot-API-Forward-Compatibility
- Repo files read: `requirements.txt`, `pyproject.toml`, `family_bot/bot.py` (parse_mode usage, `run_webhook`).
