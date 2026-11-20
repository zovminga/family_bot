# Receipt items come from the Serbian fiscal QR, Claude Vision is the fallback

A receipt photo is first scanned for the fiscal QR with `zxing-cpp` (a pip wheel, no system
libraries, so it installs on Render's native runtime). A decoded QR leads to the tax site
(suf.purs.gov.rs): the verification page, then its `POST /specifications` call for the item list.
When the QR is not found, its checksum fails, or the site does not answer, the bot reads the
photo with Claude Vision as before. This reverses the earlier "no fiscal QR / zbar" choice in
`photo_input.py`: that choice rested on `libzbar` not installing on Render, which `zxing-cpp`
removes, and the site gives exact item names and prices that Vision can misread.

## Consequences

- `/specifications` is an undocumented endpoint and can change without notice. The documented
  JSON `journal` text and Vision stay as fallbacks, so a change there degrades the result, it does
  not break photo input.
- Calls are made only when a User sends a photo: no background or bulk requests.

Research: [`docs/research/2026-11-serbian-receipt-qr.md`](../research/2026-11-serbian-receipt-qr.md).
