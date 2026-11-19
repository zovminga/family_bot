#!/usr/bin/env python3
"""Receipt photo input: Claude Vision reads a receipt and returns grouped expenses.

Pipeline (no extra services — reuses the ANTHROPIC_API_KEY already used for voice):
  1. Telegram photo/image arrives as raw bytes.
  2. Claude Vision reads the receipt: line items, prices, merchant, date, currency.
  3. Each item is classified into one of the sheet's allowed categories AND a
     coarse semantic bucket (Claude knows chips/candy/beer are snacks/alcohol,
     not "real" food).
  4. Items are grouped by that semantic bucket IN PYTHON (sum is computed here,
     not by the LLM, so the totals are reliable). This keeps "guilty pleasures"
     on their own lines — beer separate, sweets/snacks separate — while all the
     ordinary groceries collapse into ONE line instead of one row per product.

The bot then shows the SAME editable confirmation card used for voice input, so
the user can fix category/date/currency before saving to Google Sheets.

We deliberately do NOT read the fiscal QR code: Claude Vision reads the same item
names directly from the photo, works for any country/receipt, and needs no extra
system libraries (e.g. libzbar) that are painful to install on Render.
"""
from __future__ import annotations

import base64
import os
from typing import Optional

import currency

# Reuse the JSON/snap helpers from voice_input to avoid duplicating logic.
from voice_input import (
    _extract_json_object,
    _snap_amount,
    _snap_category,
    _snap_currency,
    _snap_date,
)

DEFAULT_LLM_MODEL = os.getenv("PX_LLM_MODEL", "claude-sonnet-5")

# Telegram photos are always JPEG; documents carry their own mime type.
DEFAULT_MEDIA_TYPE = "image/jpeg"
SUPPORTED_MEDIA_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}

# Keep the per-row comment readable in Telegram and tidy in the sheet.
MAX_COMMENT_LEN = 200

# Coarse semantic buckets used to split a receipt into meaningful rows. The LLM
# tags every item with one of these; we group by the bucket (NOT by the sheet
# category), so beer and sweets/snacks each get their own line while ordinary
# groceries all collapse into a single "Продукты" line.
GROUP_GROCERIES = "Продукты"
GROUP_ALCOHOL = "Алкоголь"
GROUP_SWEETS = "Сладости и снеки"


def _normalize_media_type(media_type: Optional[str]) -> str:
    """Map a Telegram mime type to one Claude accepts (fallback to JPEG)."""
    if media_type:
        mt = media_type.strip().lower()
        if mt == "image/jpg":
            mt = "image/jpeg"
        if mt in SUPPORTED_MEDIA_TYPES:
            return mt
    return DEFAULT_MEDIA_TYPE


def _build_prompt(categories: list[str], currencies: list[str],
                  today: str, who: str, default_currency: str) -> str:
    cats = "\n".join(f"- {c}" for c in categories)
    curs = ", ".join(currencies)
    codes = ", ".join(f"{c.code}→{c.symbol}" for c in currency.registered(currencies))
    return (
        "На фото — кассовый чек. Распознай его и верни СТРОГО JSON.\n\n"
        f"Сегодня: {today} (формат ДД.ММ.ГГГГ). Пользователь: {who}.\n\n"
        f"Допустимые КАТЕГОРИИ (для каждой позиции выбери ровно одну из списка):\n{cats}\n\n"
        f"Допустимые ВАЛЮТЫ (символы): {curs}. Валюта по умолчанию, если на чеке "
        f"не понять: {default_currency}.\n\n"
        "Правила:\n"
        "- Извлеки КАЖДУЮ товарную позицию: короткое человеческое название и её "
        "итоговую стоимость (уже с учётом количества, т.е. цена×кол-во).\n"
        "- amount — число (точка как десятичный разделитель), без валюты и пробелов.\n"
        "- Не включай служебные строки: ИТОГО/СУММА/СДАЧА/НДС/ПДВ/скидки/номер чека.\n"
        "- category: строго одна из списка выше.\n"
        "- group — смысловая «корзина» для группировки строк в отчёте. Правила:\n"
        f"    • Пиво, вино, крепкий алкоголь → group = «{GROUP_ALCOHOL}».\n"
        f"    • Конфеты, шоколад, чипсы, снеки, печенье, газировка, сладости → "
        f"group = «{GROUP_SWEETS}».\n"
        f"    • Обычная еда и продукты (хлеб, молоко, овощи, фрукты, мясо, крупы, "
        f"яйца, вода и т.п.) → ВСЕ в ОДНУ корзину group = «{GROUP_GROCERIES}». "
        f"НЕ дроби продукты по подтипам.\n"
        "    • Прочее (бытовая химия, косметика, товары для дома и т.п.) → "
        "group = название его category.\n"
        "- merchant: название магазина, если видно.\n"
        "- date: дата покупки с чека в формате ДД.ММ.ГГГГ. Если не видно — сегодня.\n"
        "- currency: символ валюты чека из списка выше. Код ISO на чеке переведи "
        f"в символ: {codes}.\n\n"
        "Формат ответа (без markdown, без пояснений вокруг):\n"
        '{"merchant":"...","date":"ДД.ММ.ГГГГ","currency":"дин",'
        '"items":[{"name":"...","amount":123.45,"category":"...","group":"..."}],'
        '"note":"кратко, если что-то не удалось разобрать"}'
    )


def _group_by_bucket(items: list[dict]) -> list[dict]:
    """Group snapped items by semantic bucket (item['group']).

    One draft row per bucket: amounts summed, item names listed in the comment.
    The sheet category for the row is the most common category among its items
    (a bucket like «Алкоголь» may still have to map onto whatever categories the
    sheet offers). Falls back to the sheet category as the bucket if the LLM did
    not tag a group.
    """
    from collections import Counter

    order: list[str] = []
    groups: dict[str, dict] = {}
    for it in items:
        bucket = (it.get("group") or "").strip() or it["category"]
        if bucket not in groups:
            groups[bucket] = {"amount": 0.0, "names": [], "cats": Counter()}
            order.append(bucket)
        g = groups[bucket]
        g["amount"] += it["amount"]
        g["cats"][it["category"]] += 1
        name = (it.get("name") or "").strip()
        if name:
            g["names"].append(name)

    expenses: list[dict] = []
    for bucket in order:
        g = groups[bucket]
        comment = ", ".join(g["names"])
        if len(comment) > MAX_COMMENT_LEN:
            comment = comment[: MAX_COMMENT_LEN - 1].rstrip(", ") + "…"
        # Pick the category that most items in this bucket landed in.
        category = g["cats"].most_common(1)[0][0]
        expenses.append({
            "comment": comment,
            "amount": round(g["amount"], 2),
            "category": category,
        })
    return expenses


def extract_receipt(image_bytes: bytes, media_type: Optional[str],
                    categories: list[str], currencies: list[str],
                    today: str, who: str,
                    default_currency: str = "дин",
                    model: str = DEFAULT_LLM_MODEL) -> dict:
    """Read a receipt photo and return grouped expenses.

    Returns {'expenses': [{comment, amount, currency, date, category}, ...],
             'merchant': str, 'note': str}. Each expense is one semantic bucket
             (e.g. groceries / alcohol / sweets); amounts are summed in Python.
    Requires ANTHROPIC_API_KEY in the environment.
    """
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is not set — cannot read the receipt.")

    import anthropic

    client = anthropic.Anthropic()
    prompt = _build_prompt(categories, currencies, today, who, default_currency)
    b64 = base64.standard_b64encode(image_bytes).decode("ascii")
    resp = client.messages.create(
        model=model,
        max_tokens=2048,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {
                    "type": "base64",
                    "media_type": _normalize_media_type(media_type),
                    "data": b64,
                }},
                {"type": "text", "text": prompt},
            ],
        }],
    )
    text = "".join(block.text for block in resp.content if block.type == "text")
    parsed = _extract_json_object(text)

    merchant = (parsed.get("merchant") or "").strip()
    note = (parsed.get("note") or "").strip()
    # One currency/date for the whole receipt.
    currency = _snap_currency(parsed.get("currency"), currencies, default_currency)
    date = _snap_date(parsed.get("date"), today)

    snapped: list[dict] = []
    dropped = 0
    for it in parsed.get("items") or []:
        amount = _snap_amount(it.get("amount"))
        if amount is None or amount <= 0:
            dropped += 1
            continue
        snapped.append({
            "name": (it.get("name") or "").strip(),
            "amount": amount,
            "category": _snap_category(it.get("category"), categories),
            "group": (it.get("group") or "").strip(),
        })

    expenses = _group_by_bucket(snapped)
    for e in expenses:
        e["currency"] = currency
        e["date"] = date

    if dropped:
        extra = f"пропущено позиций без цены: {dropped}"
        note = f"{note}; {extra}" if note else extra
    return {"expenses": expenses, "merchant": merchant, "note": note}
