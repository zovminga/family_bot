# Serbian fiscal receipt QR → itemized purchase list

Research for the idea: photo of a Serbian fiscal receipt → decode QR → fetch item list from the
Tax Administration (PURS) → split items by expense category. Access date for all sources: 2026-11-19.

Labels: **[verified]** = read in a primary source or measured; **[secondary]** = only a secondary
source found; **[hypothesis]** = not verified.

## Summary

- **QR content (high confidence, verified).** The QR holds a URL `https://suf.purs.gov.rs/v/?vl=<payload>`.
  The payload is a URL-encoded Base64 byte array: version, issuer UIDs, counters, total × 10000,
  timestamp (ms), invoice/transaction type, optional buyer ID, 256/512 bytes of encrypted internal
  data, a 256-byte signature, and an MD5 of everything before it. No item list in the QR.
  Header fields (total, date, invoice number) can be decoded offline; MD5 lets the bot check that
  the scan is not corrupted.
- **Endpoints (high confidence, verified live).** `GET` the same URL with `Accept: application/json`
  is an **officially documented**, unauthenticated JSON endpoint (store, totals, counters, full
  `journal` text, `isValid`). The HTML page additionally calls an **undocumented** XHR
  `POST https://suf.purs.gov.rs/specifications` (form fields `invoiceNumber`, `token`; token is a
  GUID embedded in the HTML page) that returns structured items.
- **Item data (high confidence, verified).** `/specifications` returns per item: `name`, `quantity`,
  `unitPrice`, `total`, `label` (VAT letter), `labelRate`, `taxBaseAmount`, `vatAmount`, `gtin`.
  The official JSON documents `invoiceRequest.items`, but the live response for the 2023 example
  receipt did **not** contain it — the reliable official item source is the `journal` text (UTF-8,
  fixed 40-column layout, Cyrillic headers, item names as the merchant typed them).
- **Terms / limits (medium confidence).** No auth, no captcha, no `robots.txt` (404), no terms-of-use
  link on the verification page; no rate-limit headers seen in 6 requests. The official docs say
  verification "can be performed programmatically". `/specifications` is an internal endpoint with no
  stated contract. reCAPTCHA Enterprise is used only on the manual form `/verify` (no QR).
  Rate limits: unknown.
- **Open-source parsers (high confidence, verified).** At least 8 public projects use the
  `/v/` page + `POST /specifications` flow (Go, TS, Kotlin, Python, JS); several pushed in Oct–Nov 2026.
  The most complete library is `turanjanin/serbian-fiscal-receipts-parser` (PHP, MIT: URL decoder,
  JSON and HTML fetch, journal parser). No maintained Python library that fetches items was found;
  the flow is ~60 lines with `requests`.
- **QR decoding in Python (medium-high confidence).** Use **`zxing-cpp`**: pip wheel for CPython 3.12
  manylinux x86_64 (~1.1 MB, no Python deps, no system libs). `pyzbar` needs `libzbar0` via apt,
  which Render native runtimes do not provide (zbar not in Render's preinstalled tool list; Render
  says to use Docker for missing tools). In a synthetic test (QR version 20, ECC level L, rotation /
  perspective / blur / JPEG) zxing-cpp decoded 8 of 13 cases, `cv2.QRCodeDetector` 1, WeChat
  detector without CNN models 6. All failed on crumple-like warps. Real-photo reliability is not measured.
- **Fallback (medium confidence).** Claude Vision decoding a 97×97-module QR into an ~850-char Base64
  string is **not** a realistic path (hypothesis, not tested). Keep the current Vision line-item
  reading as the fallback. The offline MD5 check also catches any wrong QR text cheaply.

## 1. What the QR code encodes

**[verified]** Official TaxCore help, "Create a QR Code": the QR contains the Verification URL.
Parameters: minimal size 40×40 mm, `ErrorCorrectionLevel = L`, `QuietZoneModules = Zero`. [S1]

**[verified]** Official TaxCore help, "Create Verification URL" — byte array layout [S2]:

| Offset | Bytes | Field | Encoding |
|---|---|---|---|
| 0 | 1 | version | current `0x03` |
| 1 | 8 | requestedBy | UID, ASCII |
| 9 | 8 | signedBy | UID, ASCII |
| 17 | 4 | totalCounter | Int32 LE |
| 21 | 4 | transactionTypeCounter | Int32 LE |
| 25 | 8 | totalAmount | amount × 10000, UInt64 LE |
| 33 | 8 | dateAndTime | Unix ms, UInt64 **BE** |
| 41 | 1 | invoiceType | 0 Normal, 1 Proforma, 2 Copy, 3 Training, 4 Advance |
| 42 | 1 | transactionType | 0 Sale, 1 Refund |
| 43 | 1 | buyer ID length | — |
| 44 | 0–20 | buyerId | ASCII |
| 44–64 | 256 or 512 | encryptedInternalData | from Secure Element |
| … | 256 | signature | from Secure Element |
| … | 16 | MD5 of all previous bytes | — |

"Created byte array is encoded as base64 string, which is additionally encoded, to comply with the
URL standards." [S2]

Consequences:

- The QR has **no item data**. Items come only from the server.
- Signed: yes (Secure Element signature + encrypted internal data). The bot cannot verify the
  signature or decrypt internal data without PURS keys **[hypothesis: no public key/procedure for
  third parties was found]**. The MD5 is an integrity check only.
- The SDC invoice number is `requestedBy-signedBy-totalCounter` (e.g. `VBMHX9SX-W6UBPZO0-76722`);
  open-source decoders build it this way [S10, S12] and it matched the live page.
- **[verified, measured]** Public example URL from the PHP library README [S10]: URL 850 chars,
  `vl` 764 Base64 chars, 572 decoded bytes, MD5 matches. The PHP decoder accepts 572–848 bytes [S10],
  so URLs are roughly 850–1250 chars. With ECC L this encodes as **QR version 20 (97×97 modules)**
  for the smallest payload (measured with the `qrcode` library). Dense code → camera resolution matters.
- Note from source code [S12]: some POS (NIS) pad buyer ID with NUL bytes; strip them.

## 2. What the verification URL returns

### 2a. HTML page (default)

**[verified live]** `GET https://suf.purs.gov.rs/v/?vl=...` → `200 text/html; charset=utf-8`,
`<html lang="sr-Cyrl-RS">`. Stable element ids used by parsers: `#invoiceNumberLabel`,
`#totalAmountLabel`, `#shopFullNameLabel`, `#tinLabel`, journal in `#collapse3 pre`.
The page contains inline JS:

```js
viewModel.InvoiceNumber('VBMHX9SX-W6UBPZO0-76722');
viewModel.Token('a01fab1c-62a4-4f9d-be00-ad5d407e64e2');
```

### 2b. Official JSON (same URL, `Accept: application/json`)

**[verified, official]** TaxCore help "Verify invoice via verification URL with JSON response" [S3]:
`GET <verification URL from QR>`, headers `Accept: application/json` (and `Content-Type:
application/json`), "No authentication is required." Response fields: `invoiceRequest` (taxId,
businessName, locationName, address, city, administrativeUnit, buyer, payments[], **items[]** with
name/quantity/unitPrice/totalPrice/gtin), `invoiceResult` (totalAmount, counters, invoiceNumber,
sdcTime), `journal` (plain text), `isValid`, `refundStatus`. "Invoice Verification Service" page
repeats this [S4].

**[verified live]** For the public example receipt the response was `200 application/json` with keys
`invoiceRequest, invoiceResult, journal, isValid`. **No `items` and no `refundStatus` keys.**
The PHP library's fixture from 2022 also has no `items` [S10]. Hypothesis: `items` is filled only for
newer receipts or is not deployed yet on the production site. Needs a test on a fresh receipt.

### 2c. Undocumented `/specifications` XHR

**[verified live + site JS]** `https://suf.purs.gov.rs/js/invoice-verify.js` (loaded by the page):

```js
$.ajax({ url: rootPath + '/specifications', type: 'POST',
         data: { invoiceNumber: self.InvoiceNumber(), token: self.Token() }, ... })
// success: if (data && data.success && data.items) { ... }
```

`rootPath = 'https://suf.purs.gov.rs'`. The same file calls `POST /recapitulation` with the same
fields (only for normal sales). Exact request reproduced:

```
POST https://suf.purs.gov.rs/specifications
Content-Type: application/x-www-form-urlencoded

invoiceNumber=<SDC invoice number>&token=<GUID from viewModel.Token(...)>
```

Response (live, public example receipt):

```json
{"success": true, "items": [
  {"gtin": "", "name": "Burito sa piletinom", "quantity": 1, "total": 650, "unitPrice": 650,
   "label": "Ђ", "labelRate": 20, "taxBaseAmount": 541.67, "vatAmount": 108.33}, ...]}
```

Same flow in open-source code: Go [S11] (form-encoded POST, token regex `viewModel\.Token\('(.*)'\);`,
retries "Refetching page for new token"), TypeScript [S13] (extracts the token with a regex, never runs
site JS, cross-checks invoice number and total against the QR, falls back to the journal), Python
Django [S12] (docstring describes the same steps). So a client needs **2 requests per receipt**:
GET HTML page → POST `/specifications`.

### 2d. TaxCore.API `verifyInvoice` — not usable

**[verified]** `POST https://<taxcore_api_url>/api/invoices/verifyInvoice` and
`.../verifyInvoiceManually` exist but require header `TaxCoreAuthenticationToken` [S5, S6]. The token
comes from "Request Authentication Token" in the SDC↔TaxCore.API protocol, i.e. an accredited
fiscal device with a certificate **[hypothesis based on the TOC location of that method; not read in
full]**. The response has no items anyway [S5].

## 3. Item list content, script, encoding

**[verified]**

- `/specifications`: name, quantity, unitPrice (with VAT), total, VAT label letter, rate,
  tax base, VAT amount, GTIN (often empty string) — see 2c. The official help describes the same
  "Invoice Specification" table on the page [S4].
- `journal`: full receipt text as printed, `\r\n` line ends, 40-column layout. Item line = name +
  VAT label in parentheses, e.g. `Burito sa piletinom (Ђ)`, then a line `price  qty  total`
  (`650,00  1  650,00`). Decimal comma, thousands dot (`1.500,00`). Followed by a VAT table
  (`Ђ  О-ПДВ  20,00%  250,00`; in the 2022 fixture also `Е  П-ПДВ  10,00%`) [S10, live].
- Script: JSON is UTF-8. Headers and VAT labels are **Cyrillic** (`ФИСКАЛНИ РАЧУН`, `Назив Цена
  Кол. Укупно`, labels `Ђ`, `Е`, `А`). Item names are free text from the merchant's POS — Latin in
  both observed examples (`JABUKA ZLATNI DELISES`, `Burito sa piletinom`). The official JSON example
  shows an English-language journal for another jurisdiction [S3], so journal language depends on
  the environment. The HTML page language can be switched (`/Home/ChangeLanguage/en-US`).
- Long names wrap over lines; some lines contain three numbers in other layouts; retailers may
  append unit and article code to names, e.g. `name/KOM/7002829` [S13 code comment; S10 commit
  "Fix parsing of receipt items which contain three different numbers"]. Prefer `/specifications`
  over parsing the journal.
- **[hypothesis]** DM receipts: item names are probably short uppercase brand/product abbreviations;
  categorisation (food vs household vs medicine) will still need an LLM or a name→category map.
  VAT label alone does not split these: 10% (Е) covers basic foods and some medicines, 20% (Ђ) most
  else **[hypothesis about VAT scope, not verified against the VAT law]**.

## 4. Terms of use, limits, auth, captcha

- **[verified]** Official docs present JSON verification as intended for programs: "Invoice
  verification can be performed programmatically by sending an HTTP request to the invoice
  verification URL" and "No authentication is required." [S3]
- **[verified live]** `/v/` and `/specifications`: no auth, no captcha, no cookies required.
  `https://suf.purs.gov.rs/robots.txt` → 404. The verification page has no terms-of-use or privacy
  link (only language switch and a link to dti.rs). Response headers show no rate-limit headers.
- **[verified live]** `https://suf.purs.gov.rs/verify` (manual check without QR) loads
  `recaptcha/enterprise.js` and posts `RecaptchaResponse` with fields `InvoiceNumberSe`,
  `SdcDateTime`, `TotalAmount`, `InvoiceCounter`, `InvoiceCounterExtension`. Do not automate it.
  The official help lists the 4 fields required for this path [S7].
- **Not found:** a published terms-of-use document or rate limit for suf.purs.gov.rs. `/specifications`
  is an internal endpoint; it can change without notice (token mechanism suggests deliberate
  anti-scraping, **hypothesis**).
- **[secondary]** A rulebook allows checking a receipt through the system for 10 years after issue
  (search-result summary of Serbian news/legal sites, primary rulebook not read).
- Recommendation: 2 requests per receipt, user-triggered only, timeout + response size limit,
  descriptive User-Agent, cache by invoice number, no bulk/background crawling.

## 5. Open-source parsers

Pushed dates from GitHub API, 2026-11-19.

| Repo | Lang | Endpoint(s) | Items? | Last push |
|---|---|---|---|---|
| [turanjanin/serbian-fiscal-receipts-parser](https://github.com/turanjanin/serbian-fiscal-receipts-parser) | PHP, MIT | `/v/` HTML (`<pre>` journal) or `Accept: application/json`; offline URL decoder | journal parser | 2026-03-18 (4★) |
| [lumpsoid/billdbgo](https://github.com/lumpsoid/billdbgo) | Go | `/v/` HTML + `POST /specifications` | yes | 2025-12-04 |
| [lumpsoid/billdb](https://github.com/lumpsoid/billdb) | Python | `/v/` + `POST /specifications` (`requests.post`) | yes | 2024-05-03 |
| [ya-erm/money-keeper](https://github.com/ya-erm/money-keeper) `src/lib/server/receipts/serbianReceipt.ts` | TS | `/v/` + `POST /specifications`, journal fallback | yes | 2026-11-19 (65★; receipt code added 2026-10-17) |
| [milicevicrajo/ims_fleet](https://github.com/milicevicrajo/ims_fleet) `nabavka/services/fiskalni.py` | Python/Django | `/v/` + `POST /specifications`; offline decode with MD5 check | yes | 2026-11-12 |
| [pejovicvuk/receiptrs](https://github.com/pejovicvuk/receiptrs) | TS | `/specifications` | yes | 2025-05-24 (15★) |
| [Innovigo/sr-invoice-parser](https://github.com/Innovigo/sr-invoice-parser) | Python (PyPI 1.0.3) | `/v/` JSON or HTML | **no** (header fields only) | 2026-01-24 (8★) |
| [TheMaskedGuardian/Fiskalni-skener](https://github.com/TheMaskedGuardian/Fiskalni-skener) | Kotlin | `/specifications` | yes | 2026-05-12 |

Others seen in code search: `mmileticc/FuelScan`, `Astrante/receipts-parser`,
`vuk-arandjelovic/QR-scanner-backend-express`, `Djordje1998/expense-tracker-v2-supabase`. All are
small, single-author projects. Best reference for this bot: `ims_fleet/fiskalni.py` (Python, offline
decode + MD5) and `money-keeper/serbianReceipt.ts` (defensive fetch: bounded body, cross-check with QR
total, journal fallback). Licences of those two were not checked.

## 6. Decoding the QR from a phone photo in Python

**Render constraint [verified]:** Render native runtimes run on Debian 12 "bookworm"; the page lists
preinstalled tools (imagemagick, ffmpeg, libvips, ghostscript, …) and says to deploy with Docker to use
a tool that is not included [S8]. zbar is not in the list. The project already notes this
(`family_bot/photo_input.py` docstring, `docs/ARCHITECTURE.md`).

| Option | Install on Render native | Notes |
|---|---|---|
| `pyzbar` 0.1.9 | **No** without Docker: README says Linux needs `sudo apt-get install libzbar0`; wheels bundle zbar only for Windows [S9]. Last release 2022. | Ruled out. |
| `opencv-python-headless` 5.1.0.95 | Yes: abi3 manylinux wheels (2_17 / 2_28), ~58–64 MB, pulls `numpy>=2` | `QRCodeDetector` passed 1/13 synthetic cases. Heavy. |
| `opencv-contrib-python-headless` (WeChat detector) | Yes, same size | `cv2.wechat_qrcode_WeChatQRCode()` without CNN model files passed 6/13; CNN models are separate downloads (not tested). Replaces the non-contrib package (same `cv2` module). |
| **`zxing-cpp` 3.1.1** | **Yes**: `cp312-abi3-manylinux_2_27/2_28_x86_64` wheel, ~1.1 MB, `requires_dist: None` [S14] | Passed 8/13, ~1–2 ms per image. Accepts NumPy arrays and Pillow images [S15]. Actively maintained (repo pushed 2026-11-18). |

**Synthetic benchmark [verified, measured; not a real-photo test].** Script generates the example URL
as a QR (version 20, ECC L) and applies distortions (`qrbench.py`, run locally in a scratch venv,
not committed). Module size ≈ 2–3 px.

| Case | zxing-cpp | cv2.QRCodeDetector | cv2 Aruco | WeChat (no model) |
|---|---|---|---|---|
| clean, 2 px/module | OK | – | – | OK |
| clean, 1.5 px/module | OK | – | – | OK |
| rotate 15° | OK | OK | OK | OK |
| rotate 45° | OK | – | – | OK |
| perspective 0.15 / 0.25 | OK / OK | – / – | – / – | – / – |
| sinusoidal warp amp 2 px / 4 px | – / – | – / – | – / – | – / – |
| blur σ1.0 + noise + JPEG 60 | OK | – | – | OK |
| blur σ1.5 + noise + JPEG 60 | – | – | – | – |
| uneven light + noise | OK | – | OK | OK |
| combo (rotate+perspective+warp+blur), 2 cases | – / – | – / – | – / – | – / – |

Takeaways:

- zxing-cpp is the best pip-only option and is fast enough to try several crops/scales per photo.
- Crumpled paper (local warp) defeated every decoder in the synthetic test. **[hypothesis]** Real
  results depend mostly on how many pixels the QR gets: version 20 needs ~97 modules across, so the QR
  should span ≥ ~300–400 px. A full-receipt photo compressed by Telegram may give too few pixels;
  the bot already uses `msg.photo[-1]` (largest size) and accepts images sent as files
  (`family_bot/bot.py`). Telegram's exact photo size limit was not checked.
- Practical tips **[hypothesis]**: ask the user for a close-up of the QR (or send as file), try
  zxing-cpp on the full image, then on upscaled / binarised crops; validate with the MD5 check.

## 7. Fallback when the QR is not readable

- **Claude Vision reading the QR [hypothesis, not tested].** The QR encodes ~850–1250 Base64 chars in a
  97×97+ module grid. Vision models read text, not bit matrices; Anthropic's docs list no QR support
  and warn about mistakes on "low-quality, rotated, or very small images" [S16]. Treat as unworkable.
  If tried anyway, the MD5 check rejects a wrong result at no cost.
- **Claude Vision reading the printed SDC fields → server lookup: blocked.** Manual verification
  without a QR needs total, SDC time, SDC invoice number and counter [S7], but the public form uses
  reCAPTCHA Enterprise [live], and the API variant needs a TaxCore token [S6].
- **Claude Vision reading item lines [current behaviour].** Keep `photo_input.py` as the fallback.
  The printed receipt has the same 40-column journal layout as the server's `journal`, so the Vision
  prompt can rely on "name (VAT label)" + "price qty total" lines. Vision downscales images to
  1,568 px (standard tier) or 2,576 px (Claude 4.7+ "high-resolution" tier) on the long edge [S16];
  long receipts may need cropping into parts.
- Suggested flow: zxing-cpp → if decoded and MD5 OK → GET page + POST `/specifications` → (if that
  fails) official JSON `journal` → (if offline/failed) Vision item reading. In all cases cross-check
  the item sum against the QR total.

## Open questions

1. Does the official JSON contain `invoiceRequest.items` for receipts issued in 2025–2026? Test with a
   fresh real receipt (only the user's own, not committed).
2. Does `/specifications` work for DM receipts and return useful names / GTINs? GTIN would allow a
   product-category lookup.
3. Token lifetime and whether `/specifications` rate-limits or blocks datacenter IPs (Render egress).
   Only 6 requests were sent from one non-datacenter IP.
4. Real-photo decode rate of zxing-cpp on DM receipts sent via Telegram (compressed photo vs file).
5. Whether PURS publishes any terms of use or API policy for suf.purs.gov.rs (none found).
6. Exact VAT label → product type mapping in Serbian VAT law (not checked).

## Sources

Primary unless marked. All accessed 2026-11-19.

- [S1] TaxCore Help (PURS), "Create a QR Code" — https://tap.suf.purs.gov.rs/help/view/1920647237/Create-a-QR-Code/en-US
- [S2] TaxCore Help, "Create Verification URL" — https://tap.suf.purs.gov.rs/help/view/1920647237/Create-Verification-URL/en-US
- [S3] TaxCore Help, "Verify invoice via verification URL with JSON response" — https://tap.suf.purs.gov.rs/help/view/145107305/Verify-invoice-via-verification-URL-with-JSON-response/en-US
- [S4] TaxCore Help, "Invoice Verification Service" — https://tap.suf.purs.gov.rs/help/view/1665995733/Invoice-Verification-Service/en-US
- [S5] TaxCore Help, "Verify Invoice" (TaxCore.API) — https://tap.suf.purs.gov.rs/help/view/1342300506/Verify-Invoice/en-US
- [S6] TaxCore Help, "Verify Invoice Manually" (TaxCore.API) — https://tap.suf.purs.gov.rs/help/view/1342300506/Verify-Invoice-Manually/en-US
- [S7] TaxCore Help, "Invoice Verification Without a QR Code or Verification URL" — https://tap.suf.purs.gov.rs/help/view/145107305/Invoice-Verification-Without-a-QR-Code-or-Verification-URL/en-US
  (Help pages render via JS; article bodies were read from `https://tap.suf.purs.gov.rs/help/Home/GetContent?hash=<id>&file=<name>`.)
- [S8] Render docs, "Native Runtimes" — https://render.com/docs/native-runtimes ; "Docker on Render" — https://docs.render.com/docker
- [S9] pyzbar README — https://github.com/NaturalHistoryMuseum/pyzbar
- [S10] turanjanin/serbian-fiscal-receipts-parser (`src/UrlDecoder.php`, `src/Fetcher.php`, `src/Parser.php`, `tests/Fixtures/1.json`, README) — https://github.com/turanjanin/serbian-fiscal-receipts-parser
- [S11] lumpsoid/billdbgo `internal/parser/serbia/serbia.go` — https://github.com/lumpsoid/billdbgo
- [S12] milicevicrajo/ims_fleet `nabavka/services/fiskalni.py` — https://github.com/milicevicrajo/ims_fleet
- [S13] ya-erm/money-keeper `src/lib/server/receipts/serbianReceipt.ts` — https://github.com/ya-erm/money-keeper
- [S14] PyPI JSON metadata: https://pypi.org/pypi/zxing-cpp/json , https://pypi.org/pypi/opencv-python-headless/json , https://pypi.org/pypi/pyzbar/json
- [S15] zxing-cpp Python README — https://github.com/zxing-cpp/zxing-cpp/tree/master/wrappers/python
- [S16] Anthropic docs, "Vision" (resolution tiers, limitations) — https://platform.claude.com/docs/en/build-with-claude/vision
- Live observations (6 requests to suf.purs.gov.rs, using the public example URL from the [S10] README:
  `GET /v/` JSON, `GET /v/` HTML, `GET /js/invoice-verify.js`, `POST /specifications`; plus
  `GET /verify` and `GET /robots.txt`) — https://suf.purs.gov.rs
- [secondary] 10-year verification window: web search summary citing Serbian news / KPMG articles on fiscalization rulebooks, e.g. https://kpmg.com/rs/sr/analize-i-istrazivanja/poreske-vesti/2021/05/usvojeni-su-novi-pravilnici-koji-se-odnose-na-primenu-zakona-o-fiskalizaciji.html
