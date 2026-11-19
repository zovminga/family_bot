#!/usr/bin/env python3
"""Build a standalone HTML dashboard for family expenses.

Pipeline:
  1. Connect to the same Google Sheet the Telegram bot writes to (SheetsClient).
  2. Parse rows robustly (English/Russian headers, comma decimals).
  3. Convert every amount to a base currency (default RSD) using live FX rates.
  4. Group free-text comments into themes (rule-based by default; optional LLM).
  5. Inject the resulting JSON into template.html and write a self-contained file.

The output HTML embeds all data inline, so it opens with a double click and can
be shared as a single file. It is written to ./output/ which is git-ignored,
because it contains real financial data and this repo is public.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import traceback
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

from family_bot.dashboard.failure_alert import ALERT_HANDLED_EXIT_CODE, send_failure_alert
from family_bot.sheets import open_client
from family_bot.sheet_data import (
    ISO_TO_SYMBOL,
    SYMBOL_TO_ISO,
    normalize_month,
    parse_amount,
    resolve_columns,
)

# ---------------------------------------------------------------------------
# Paths & configuration
# ---------------------------------------------------------------------------
# Code lives in family_bot/dashboard/; the template, output/ and vendor/ stay
# in the repo-root dashboard/ folder next to run.sh.
BOT_DIR = Path(__file__).resolve().parents[2]  # repo root (.env)
DASHBOARD_DIR = BOT_DIR / "dashboard"
OUTPUT_DIR = DASHBOARD_DIR / "output"
TEMPLATE_PATH = DASHBOARD_DIR / "template.html"
DATA_PLACEHOLDER = "__DASHBOARD_DATA__"

# Chart.js is inlined into the output so the HTML is fully self-contained and
# renders offline / inside in-app browsers (Telegram) without hitting a CDN.
CHARTJS_PLACEHOLDER = "__CHARTJS__"
CHARTJS_CDN = "https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"
VENDOR_DIR = DASHBOARD_DIR / "vendor"
CHARTJS_PATH = VENDOR_DIR / "chart.umd.min.js"

# Per-provider default model for LLM comment grouping.
DEFAULT_LLM_MODELS = {
    "anthropic": "claude-sonnet-5",   # stronger reasoning for comment classification
    "openai": "gpt-4o-mini",
}


def cache_path(provider: str) -> Path:
    """Cache file is provider-specific so switching provider re-classifies."""
    return OUTPUT_DIR / f"llm_cache_{provider}.json"

# Spender name normalization (Latin/Cyrillic + nickname → one canonical name).
# NOTE: "Лиза" (July) and "snezhi" (from August) are treated as the same person.
SPENDER_NORMALIZE = {
    "азат": "Азат", "azat": "Азат",
    "лиза": "Лиза", "lisa": "Лиза", "snezhi": "Лиза", "снежи": "Лиза", "лиса": "Лиза",
}

# Comments matching this are ATM cash withdrawals / cash-outs, not real
# spending — they only move money between accounts. Excluded from the dashboard
# entirely (not sent to the LLM, not counted in any total).
EXCLUDE_COMMENT_RE = re.compile(
    r"снятие\s*налич|снятие\s*денег|обнал|mobilni\s*ke[sš]|smart\s*atm|банкомат",
    re.IGNORECASE,
)


# Ordered keyword → theme rules (first match wins). Encodes the semantic
# grouping of free-text comments without calling an external LLM.
THEME_RULES: list[tuple[str, list[str]]] = [
    ("Подписки и сервисы", ["chatgpt", "cursor", "ps store", "psn", "ps5", "linkedin",
                            "линкедин", "spotify", "спотифай", "airalo", "sim", "internet",
                            "интернет", "yettel", "plasticity", "migaku", "мигаку",
                            "games", "game", "скин"]),
    ("Жильё и коммуналка", ["аренд", "коммунал", "копия ключей"]),
    ("Такси", ["такси", "taxi", "так си"]),
    ("Метро и транспорт", ["метро", "автобус", "поезд", "train", "синкансен",
                           "kamikochi", "кама кура", "морю"]),
    ("Спорт и танцы", ["танцы", "фитпасс", "fitpass", "трена", "тренировк", "креатин"]),
    ("Красота", ["маникюр", "педикюр", "брови", "стрижка", "массаж", "окрашивание",
                 "косметолог", "салон", "ламинир", "ретейнер", "очки", "сауна",
                 "шампунь", "крем", "маск", "косметик", "гель", "полотенц", "расческ",
                 "расчёск", "санскрин", "мыло", "салфетк", "пилк", "средства для волос",
                 "сыворотк", "духи", "парфюм", "zone"]),
    ("Здоровье и аптека", ["аптек", "витамин", "анализ", "таблетк", "лор", "стоматолог",
                           "кариес", "коронка", "vizim", "психолог", "зубн", "паразит",
                           "лекарств"]),
    ("Кофе", ["sonder", "surf", "старбакс", "starbucks", "latte", "латте", "кофе", "coffee"]),
    ("Суши и рамен", ["рамен", "ramen", "суши", "sushi", "ичиран", "afuri", "куросуши",
                      "кура суши", "go sushi", "sushirito"]),
    ("Фастфуд и конбини", ["мак", "mcdo", "mcdonald", "макдак", "бургер", "burger",
                           "kfs", "кфс", "shake shack", "family mart", "familymart",
                           "конбини", "комбини", "lawson", "7eleven", "7 eleven", "осам",
                           "бурек"]),
    ("Доставка (Wolt и пр.)", ["wolt", "wilt", "вольт", "кафетери", "kafeterija", "embers",
                               "sloj", "zaokret", "березка", "плескавица", "паста",
                               "карбонара", "пицца", "плов", "frenzy"]),
    ("Кафе и рестораны", ["кафе", "ресторан", "обед", "ужин", "завтрак", "бранч", "ланч",
                          "bobo", "gosti", "berliner", "крокеты", "hotel beograd", "june",
                          "джун", "7:00", "focus", "стамба", "сендвич", "сэндвич",
                          "попоболены"]),
    ("Дом и быт", ["temu", "jysk", "икеа", "ikea", "pepco", "контейнер", "чайник",
                   "вентилятор", "кружка", "наволочк", "строительн", "отвертк", "петля",
                   "посудин", "уборка", "анжела", "lily", "штанген", "наждачк",
                   "клей", "скотч", "dm", "дм"]),
    ("Супермаркет и продукты", ["maxi", "макси", "меркатор", "vero", "веро", "супер", "идея",
                                "ah", "woltmarket", "wolt market", "продукт", "супермаркет",
                                "овощи", "рыба", "заморозк", "mali kalenic", "kfood", "корейск",
                                "арома", "aroma", "комбини", "онигири", "бенто",
                                "нудлс", "cup noodles", "силк", "to-to", "супы", "вода",
                                "водичка", "ритер", "фрикадельк", "чай"]),
    ("Алкоголь", ["пиво", "вино", "глинтвейн"]),
    ("Сладости и снеки", ["снек", "чипсы", "конфет", "сникерс", "баунти", "мороженое",
                          "десерт", "круассан", "плюшк", "булк", "кулич", "дрип", "матча"]),
    ("Одежда и обувь", ["uniqlo", "юникло", "кроссов", "кроссы", "джинс", "юбк", "футболк",
                        "носки", "тапки", "туфли", "плать", "куртка", "шапка", "шарф",
                        "сумка", "кольца", "панам", "термобелье", "костюм", "грамичи",
                        "arket", "кодзима", "украшен"]),
    ("Шопинг и магазины", ["loft", "muji", "муджи", "донки", "донкихот", "канцеляр",
                           "художествен", "дизайнерск", "драгстор", "kengur", "китайк",
                           "доставка из германии"]),
    ("Техника", ["airpods", "мышка", "наушник", "ноутбук", "телефон", "зарядк",
                 "клавиатур", "монитор", "принтер", "3д", "3d"]),
    ("Путешествия", ["билет", "перелет", "перелёт", "самолет", "самолёт", "flixbus", "numa",
                     "страховк", "токио", "берлин", "милан", "amsterdam", "belgrad",
                     "барселон", "роттердам"]),
    ("Подарки", ["подар", "букет", "цветы", "открытк"]),
    ("Документы и бюрократия", ["юрист", "пошлин", "доверенность", "внж", "нотариус",
                                "уверенье", "консульств", "виза", "апостил"]),
    ("Образование", ["english lessons", "lesson", "урок", "репетитор", "обучен",
                     "duolingo", "italki", "preply", "language school", "tutor",
                     "курсы", "вебинар", "семинар"]),
    ("Развлечения и музеи", ["музе", "museum", "книг", "журнал", "гача", "замок", "зоопарк",
                             "кинотеатр", "кино", "бильярд", "компьютерный клуб", "cs", "кс",
                             "пленк", "проявк", "парк", "park", "сад", "pokemon",
                             "pokémon", "покемон", "плюшев", "стикер", "sylvanian",
                             "минипиги", "газет"]),
]


# Regression guard: known comment → expected theme. These pairs encode the
# substring-collision bugs we already fixed, so adding a new keyword that
# breaks one of them fails loudly at startup instead of silently miscategorizing.
CLASSIFY_SELFTEST = {
    "Фитпасс": "Спорт и танцы",       # must not match "пасс" (метро)
    "Fitpass": "Спорт и танцы",
    "Чайник": "Дом и быт",            # must beat "чай" (продукты)
    "DM": "Дом и быт",                # moved out of продукты
    "Maxi": "Супермаркет и продукты",
    "Pokémon": "Развлечения и музеи",
    "Покупка продуктов": "Супермаркет и продукты",  # must not match "пок" (покемон)
    "Аренда квартиры": "Жильё и коммуналка",
    "English lessons": "Образование",
    "Easy frenzy": "Доставка (Wolt и пр.)",   # meal-kit delivery, not groceries
}


def _run_classify_selftest() -> None:
    failures = [
        f"{comment!r}: expected {expected!r}, got {classify_theme(comment)!r}"
        for comment, expected in CLASSIFY_SELFTEST.items()
        if classify_theme(comment) != expected
    ]
    if failures:
        raise AssertionError(
            "THEME_RULES regression detected:\n  " + "\n  ".join(failures)
        )


# Category repairs pick a name that already exists in the sheet (Config list or
# Data), so the dashboard never shows a Category the household did not create.
RENT_CATEGORY_RE = re.compile(r"^(rent|аренд)", re.IGNORECASE)
BILLS_CATEGORY_RE = re.compile(r"^(bills|коммунал|счета)", re.IGNORECASE)
NO_CATEGORY = "Без категории"
RENT_COMMENT_WORD = "аренд"       # comment says the row is rent
BILLS_COMMENT_WORD = "коммунал"   # comment says the row is utility bills


def category_order(config: list[str], seen: list[str]) -> list[str]:
    """
    Category names for the page: Config sheet order, then the rest by name.

    Parameters
    ----------
    config : list[str]
        Names from column A of the Config sheet (may hold blanks and repeats).
    seen : list[str]
        Category names found in Data rows.

    Returns
    -------
    list[str]
        Each name once: Config names first in sheet order, then names that
        appear only in Data, sorted.
    """
    order: list[str] = []
    for name in config:
        name = name.strip()
        if name and name not in order:
            order.append(name)
    order += sorted({s for s in seen if s and s not in order})
    return order


def _known(known: list[str], pattern: re.Pattern[str]) -> Optional[str]:
    """
    First name in ``known`` that matches ``pattern``.

    Parameters
    ----------
    known : list[str]
        Category names of this sheet.
    pattern : re.Pattern[str]
        Name prefix to look for.

    Returns
    -------
    Optional[str]
        The matching name, or None.
    """
    return next((name for name in known if pattern.match(name)), None)


def repair_category(category: str, comment: str, known: list[str]) -> str:
    """
    Fix a Category cell that was left empty, overwritten, or used for rent.

    Parameters
    ----------
    category : str
        Category cell from Data.
    comment : str
        Comment cell from Data.
    known : list[str]
        Category names of this sheet (Config and Data).

    Returns
    -------
    str
        The cell as is, or a name from ``known``: the rent Category when the
        comment says «аренд», the bills Category for an empty or numeric cell
        with «коммунал». An empty or numeric cell with no match gets
        ``NO_CATEGORY``.
    """
    comment_l = comment.lower()
    if RENT_COMMENT_WORD in comment_l:
        rent = _known(known, RENT_CATEGORY_RE)
        if rent:
            return rent
    if not category or category.replace(".", "").replace(",", "").isdigit():
        bills = _known(known, BILLS_CATEGORY_RE) if BILLS_COMMENT_WORD in comment_l else None
        return bills or NO_CATEGORY
    return category


def normalize_spender(name: str) -> str:
    if not name:
        return "—"
    return SPENDER_NORMALIZE.get(name.strip().lower(), name.strip())


def classify_theme(comment: str) -> Optional[str]:
    """Return a semantic theme for a comment, or None if no rule matches."""
    if not comment or not comment.strip():
        return None
    text = comment.lower()
    for theme, keywords in THEME_RULES:
        if any(kw in text for kw in keywords):
            return theme
    return None

# ---------------------------------------------------------------------------
# FX rates
# ---------------------------------------------------------------------------
def fetch_rates(base_iso: str) -> dict[str, float]:
    """Return {ISO: units_of_ISO_per_1_base} for converting into the base currency."""
    url = f"https://api.exchangerate-api.com/v4/latest/{base_iso}"
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    rates = resp.json().get("rates", {})
    rates[base_iso] = 1.0
    return rates


def get_chartjs() -> str:
    """Return the Chart.js UMD bundle to inline, caching it under vendor/.

    Downloaded once from the CDN and cached on disk so subsequent builds work
    offline. The output HTML embeds this so it renders without any network.
    """
    if CHARTJS_PATH.exists():
        return CHARTJS_PATH.read_text(encoding="utf-8")
    print("→ Fetching Chart.js to inline (first run; cached under vendor/)…")
    resp = requests.get(CHARTJS_CDN, timeout=30)
    resp.raise_for_status()
    VENDOR_DIR.mkdir(parents=True, exist_ok=True)
    CHARTJS_PATH.write_text(resp.text, encoding="utf-8")
    return resp.text


def to_base(amount: float, iso: str, base_iso: str, rates: dict[str, float]) -> Optional[float]:
    """Convert `amount` in `iso` to `base_iso`. rates are units per 1 base."""
    if iso == base_iso:
        return amount
    rate = rates.get(iso)
    if not rate:
        return None
    return amount / rate


def unconvertible_rows_error(rows: list[dict]) -> Optional[str]:
    """Describe rows whose Currency has no FX rate, or None when all converted.

    Such a row would count as 0 in every base-currency total, so the build stops
    instead of publishing a dashboard that silently undercounts.
    """
    missing = [r for r in rows if r["amount_base"] is None]
    if not missing:
        return None
    lines = [f"  {r['date'] or '(no date)'} · {r['amount']:g} {r['currency_symbol']} "
             f"({r['currency']}) · {r['who']} · {r['comment'] or '(no comment)'}"
             for r in missing]
    return (f"{len(missing)} row(s) have a Currency with no FX rate. Fix the Currency "
            "cell in the sheet or add the currency to currency.py:\n" + "\n".join(lines))


# Characters that must not appear raw inside an inline <script>: "<" could
# start "</script>" or "<!--", U+2028/U+2029 break older JS parsers.
_SCRIPT_UNSAFE = {"<": "\\u003c", ">": "\\u003e", "&": "\\u0026",
                  "\u2028": "\\u2028", "\u2029": "\\u2029"}


def json_for_script(data: object) -> str:
    """Serialize `data` as JSON that is safe to paste inside a <script> element."""
    payload = json.dumps(data, ensure_ascii=False)
    return "".join(_SCRIPT_UNSAFE.get(ch, ch) for ch in payload)


# ---------------------------------------------------------------------------
# LLM comment grouping
# ---------------------------------------------------------------------------
def load_cache(path: Path) -> dict[str, str]:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def save_cache(path: Path, cache: dict[str, str]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


# Canonical theme taxonomy. The dashboard's optimization tiers (THEME_TIER in
# template.html) key off these EXACT names, so the LLM must classify into this
# closed set instead of inventing free-form labels — otherwise tiering silently
# falls back to category. "Прочее" is the escape hatch for anything that fits none.
CANONICAL_THEMES: list[str] = [theme for theme, _ in THEME_RULES]
# Case-insensitive lookup, precomputed once (used per-comment in _snap_to_canonical).
_CANONICAL_LOOKUP: dict[str, str] = {t.lower(): t for t in CANONICAL_THEMES}


def build_grouping_prompt(batch: list[str]) -> str:
    """Prompt shared by every LLM provider: classify comments into canonical themes."""
    allowed = "\n".join(f"- {t}" for t in CANONICAL_THEMES)
    return (
        "Ты помощник по личным финансам. Для каждого комментария к трате определи "
        "ТЕМУ, выбрав СТРОГО ОДНУ из списка допустимых тем ниже. Не придумывай "
        "новые темы и не меняй их написание. Если ничего не подходит — верни "
        "'Прочее'.\n\n"
        f"Допустимые темы:\n{allowed}\n- Прочее\n\n"
        "Верни СТРОГО JSON-объект вида {\"комментарий\": \"Тема\", ...} без "
        "пояснений и без markdown-обёртки.\n\n"
        "Комментарии:\n" + "\n".join(f"- {c}" for c in batch)
    )


def _extract_json_object(text: str) -> dict:
    """Parse a JSON object from a model reply, tolerating ```json fences."""
    text = text.strip()
    if text.startswith("```"):
        # Strip a leading ```json / ``` fence and the trailing ```.
        text = text.split("\n", 1)[-1] if "\n" in text else text
        text = text.rsplit("```", 1)[0].strip()
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start:end + 1]
    return json.loads(text)


def _classify_batch_openai(prompt: str, model: str) -> dict[str, str]:
    from openai import OpenAI

    client = OpenAI()
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        response_format={"type": "json_object"},
    )
    return _extract_json_object(resp.choices[0].message.content)


def _classify_batch_anthropic(prompt: str, model: str) -> dict[str, str]:
    import anthropic

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment
    # NOTE: newer models (e.g. Sonnet 5) reject `temperature`; omit it so the
    # classifier works across models. The closed theme list keeps output stable.
    resp = client.messages.create(
        model=model,
        max_tokens=8192,  # a full batch of comment→theme pairs can exceed 4k tokens
        messages=[{"role": "user", "content": prompt}],
    )
    # content is a list of blocks; concatenate the text blocks.
    text = "".join(block.text for block in resp.content if block.type == "text")
    return _extract_json_object(text)


# provider -> (env var it needs, per-batch classifier).
LLM_PROVIDERS = {
    "anthropic": ("ANTHROPIC_API_KEY", _classify_batch_anthropic),
    "openai": ("OPENAI_API_KEY", _classify_batch_openai),
}


def _snap_to_canonical(theme: Optional[str]) -> str:
    """Force an LLM answer onto the canonical taxonomy so THEME_TIER keys match.

    Case-insensitive match to a canonical theme; anything else becomes 'Прочее'.
    """
    if not theme or not theme.strip():
        return "Прочее"
    return _CANONICAL_LOOKUP.get(theme.strip().lower(), "Прочее")


def group_comments_llm(comments: list[str], provider: str, model: str) -> dict[str, str]:
    """Map each unique non-empty comment to a short normalized theme via an LLM.

    Results are cached on disk (per provider) keyed by the comment text, so
    re-runs only send comments the model has not seen before. `provider` selects
    the backend: "anthropic" (Claude) or "openai".
    """
    if provider not in LLM_PROVIDERS:
        raise ValueError(f"Unknown LLM provider: {provider!r}")
    env_var, classify_batch = LLM_PROVIDERS[provider]

    path = cache_path(provider)
    cache = load_cache(path)
    unique = sorted({c.strip() for c in comments if c and c.strip()})
    pending = [c for c in unique if c not in cache]

    if pending and os.getenv(env_var):
        # Batch to keep prompts small and resilient.
        batch_size = 60
        for start in range(0, len(pending), batch_size):
            batch = pending[start:start + batch_size]
            try:
                mapping = classify_batch(build_grouping_prompt(batch), model)
            except Exception as exc:  # noqa: BLE001 - degrade gracefully
                # Not cached: the build falls back to rules for these comments
                # and the next run asks the LLM again.
                print(f"⚠️  {provider} grouping failed for a batch, will retry next run: {exc}",
                      file=sys.stderr)
                continue
            for comment in batch:
                cache[comment] = _snap_to_canonical(mapping.get(comment))
        save_cache(path, cache)
    elif pending:
        print(f"⚠️  {env_var} not set — comments left ungrouped ('Без темы').",
              file=sys.stderr)
        for comment in pending:
            cache.setdefault(comment, "Без темы")

    return cache


# ---------------------------------------------------------------------------
# Main build
# ---------------------------------------------------------------------------
def read_csv(path: Path) -> tuple[list[str], list[list[str]]]:
    """Read a CSV export of the 'Data' sheet, returning (headers, rows)."""
    import csv

    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = list(csv.reader(fh))
    if len(reader) < 2:
        raise RuntimeError(f"CSV '{path}' has no data rows.")
    return reader[0], reader[1:]


def build(base_iso: str, model: str, use_llm: bool, provider: str = "anthropic",
          csv_path: Optional[str] = None, from_month: Optional[str] = None,
          as_of: Optional[date] = None) -> Path:
    load_dotenv(BOT_DIR / ".env")

    spreadsheet_url = None
    config_categories: list[str] = []
    if csv_path:
        print(f"→ Reading data from CSV: {csv_path}")
        headers, raw_rows = read_csv(Path(csv_path))
    else:
        print("→ Connecting to Google Sheets…")
        client = open_client()
        spreadsheet_url = client.url
        values = client.read_data_values()
        if len(values) < 2:
            raise RuntimeError("Sheet 'Data' has no rows to analyze.")
        headers, raw_rows = values[0], values[1:]
        config_categories = client.read_categories()
    cols = resolve_columns(headers)
    for required in ("amount", "currency", "category"):
        if required not in cols:
            raise RuntimeError(f"Required column '{required}' not found. Headers: {headers}")

    def cell(row: list[str], field: str) -> str:
        idx = cols.get(field)
        if idx is None or idx >= len(row):
            return ""
        return str(row[idx]).strip()

    # Merge manual adjustments (e.g. rent entries the user forgot to log).
    # Rows are aligned to the main headers by field name, so column order is
    # irrelevant. The file is git-ignored and stays out of the public repo.
    adj_path = DASHBOARD_DIR / "manual_adjustments.csv"
    if adj_path.exists():
        a_headers, a_rows = read_csv(adj_path)
        a_cols = resolve_columns(a_headers)
        for ar in a_rows:
            aligned = [""] * len(headers)
            for field, idx in cols.items():
                a_idx = a_cols.get(field)
                if a_idx is not None and a_idx < len(ar):
                    aligned[idx] = ar[a_idx]
            raw_rows.append(aligned)
        print(f"→ Merged {len(a_rows)} manual adjustment row(s) from {adj_path.name}.")

    print(f"→ Parsed {len(raw_rows)} raw rows. Fetching FX rates (base={base_iso})…")
    rates = fetch_rates(base_iso)

    rows: list[dict] = []
    skipped = 0
    excluded = 0
    comments: list[str] = []
    for row in raw_rows:
        amount = parse_amount(cell(row, "amount"))
        if amount is None:
            skipped += 1
            continue
        comment = cell(row, "comment")
        # Cash withdrawals are transfers, not spending — drop them before they
        # reach the totals or the LLM classifier.
        if EXCLUDE_COMMENT_RE.search(comment):
            excluded += 1
            continue
        symbol = cell(row, "currency")
        iso = SYMBOL_TO_ISO.get(symbol, symbol.upper() if symbol else base_iso)
        amount_base = to_base(amount, iso, base_iso, rates)
        comments.append(comment)
        date = cell(row, "date")
        rows.append({
            "date": date,
            "month": normalize_month(date, cell(row, "month")),
            "category": cell(row, "category"),
            "amount": round(amount, 2),
            "currency": iso,
            "currency_symbol": ISO_TO_SYMBOL.get(iso, symbol or iso),
            "amount_base": round(amount_base, 2) if amount_base is not None else None,
            "who": normalize_spender(cell(row, "who")),
            "comment": comment,
        })

    # Repair empty / numeric / rent-mislogged Category cells with this sheet's own names.
    known = category_order(config_categories, [r["category"] for r in rows])
    for r in rows:
        r["category"] = repair_category(r["category"], r["comment"], known)

    print(f"→ Kept {len(rows)} rows, skipped {skipped} unparseable, "
          f"excluded {excluded} cash withdrawal(s).")

    # Optionally hide early periods (e.g. data before the household settled in).
    if from_month:
        before = len(rows)
        rows = [r for r in rows if r["month"] and r["month"] >= from_month]
        print(f"→ Filtered to months >= {from_month}: kept {len(rows)}, hid {before - len(rows)}.")

    rate_error = unconvertible_rows_error(rows)
    if rate_error:
        raise RuntimeError(rate_error)

    if use_llm:
        print(f"→ Grouping comments into themes via LLM ({provider}, model={model})…")
        theme_map = group_comments_llm(comments, provider, model)
        # Hybrid: the LLM is great at semantics but doesn't know local venue/brand
        # names (e.g. "Bobo", "Sloj"). For anything it left as "Прочее", fall back
        # to the keyword rules, which encode that local knowledge. Keeps "Прочее"
        # to genuinely unclassifiable comments.
        fallback_hits = 0
        for r in rows:
            key = r["comment"].strip()
            if not key:
                r["theme"] = "Без комментария"
                continue
            theme = theme_map.get(key, "Прочее")
            if theme == "Прочее":
                ruled = classify_theme(key)
                if ruled:
                    theme = ruled
                    fallback_hits += 1
            r["theme"] = theme
        if fallback_hits:
            print(f"→ Rules fallback re-classified {fallback_hits} row(s) out of 'Прочее'.")
    else:
        print("→ Grouping comments into themes via rules…")
        for r in rows:
            if not r["comment"].strip():
                r["theme"] = "Без комментария"
            else:
                r["theme"] = classify_theme(r["comment"]) or "Прочее"

    data = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        # build date: the first screen's "this month" and how many of its days passed
        "as_of": (as_of or datetime.now().date()).isoformat(),
        "base_currency": base_iso,
        "base_symbol": ISO_TO_SYMBOL.get(base_iso, base_iso),
        "spreadsheet_url": spreadsheet_url,
        "rates": {k: rates.get(k) for k in {r["currency"] for r in rows}},
        "theme_rules": ({} if use_llm else {t: kws for t, kws in THEME_RULES}),
        "grouping_mode": (provider if use_llm else "rules"),
        "from_month": from_month,
        # colour order: Config sheet first, so a Category keeps its colour.
        # Taken after the repair, so repaired names and NO_CATEGORY are listed too.
        "categories": category_order(config_categories, [r["category"] for r in rows]),
        "rows": rows,
    }

    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    # Inline Chart.js so the file is self-contained (works offline / in-app browsers).
    # Chart.js goes in first: data inserted last is never scanned for placeholders.
    html = template.replace(CHARTJS_PLACEHOLDER, get_chartjs())
    html = html.replace(DATA_PLACEHOLDER, json_for_script(data))

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / "expenses_dashboard.html"
    out_path.write_text(html, encoding="utf-8")
    print(f"✅ Dashboard written to {out_path}")
    return out_path


# ---------------------------------------------------------------------------
# Publish to Telegram (build machine only)
# ---------------------------------------------------------------------------
def _store_file_id_in_sheet(file_id: str) -> None:
    """Write the latest dashboard file_id + timestamp to the Meta worksheet.

    The Telegram bot reads this cell to re-send the latest dashboard on demand.
    """
    open_client().set_dashboard_file_id(file_id)

def publish_to_telegram(html_path: Path) -> None:
    """Upload the built dashboard to Telegram and store its file_id in the sheet.

    Runs on the build machine after a successful build so the bot can later
    re-send the latest dashboard by file_id without rebuilding anything.
    Requires BOT_TOKEN and DASHBOARD_CHAT_ID in the environment (.env).
    """
    token = os.getenv("BOT_TOKEN")
    chat_id = os.getenv("DASHBOARD_CHAT_ID")
    if not token or not chat_id:
        print("⚠️  BOT_TOKEN/DASHBOARD_CHAT_ID not set — skipping Telegram publish.",
              file=sys.stderr)
        return

    print("→ Publishing dashboard to Telegram…")
    try:
        with html_path.open("rb") as fh:
            resp = requests.post(
                f"https://api.telegram.org/bot{token}/sendDocument",
                data={"chat_id": chat_id,
                      "caption": "🔄 Актуальный дашборд трат обновлён."},
                files={"document": ("expenses_dashboard.html", fh, "text/html")},
                timeout=120,
            )
        resp.raise_for_status()
    except requests.RequestException as exc:
        # The request URL holds the token and requests puts it into the error
        # text, which run.sh tees into build.log / launchd.log. Re-raise a copy
        # with the token masked and without the original chained traceback.
        raise type(exc)(str(exc).replace(token, "<BOT_TOKEN>")) from None
    result = resp.json()
    if not result.get("ok"):
        # Raise so main() reports the failed publish to the Dashboard chat.
        raise RuntimeError(f"Telegram publish failed: {result}")

    file_id = result["result"]["document"]["file_id"]
    _store_file_id_in_sheet(file_id)
    print("✅ Published to Telegram and stored file_id in the 'Meta' sheet.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the family expenses dashboard.")
    parser.add_argument("--base", default="RSD", help="Base currency ISO code (default: RSD).")
    parser.add_argument("--provider", choices=list(LLM_PROVIDERS), default="anthropic",
                        help="LLM backend for comment grouping (default: anthropic/Claude).")
    parser.add_argument("--model", default=None,
                        help="Model for grouping. Defaults per provider "
                             "(Claude: claude-sonnet-5, OpenAI: gpt-4o-mini).")
    parser.add_argument("--llm", action="store_true",
                        help="Use an LLM for comment grouping (needs the provider's API key: "
                             "ANTHROPIC_API_KEY or OPENAI_API_KEY). "
                             "Default: built-in rule-based grouping.")
    parser.add_argument("--csv", default=None, help="Build from a CSV export instead of Google Sheets.")
    parser.add_argument("--from", dest="from_month", default="2025-10",
                        help="Hide data before this month (YYYY-MM). Use '' to include everything.")
    parser.add_argument("--publish", action="store_true",
                        help="After building, upload the HTML to Telegram and store its "
                             "file_id in the 'Meta' sheet (needs BOT_TOKEN + DASHBOARD_CHAT_ID).")
    args = parser.parse_args()

    stage = "самопроверка классификатора"
    try:
        _run_classify_selftest()
        base = SYMBOL_TO_ISO.get(args.base, args.base.upper())
        model = args.model or DEFAULT_LLM_MODELS[args.provider]
        stage = "сборка"
        out_path = build(base_iso=base, model=model, use_llm=args.llm, provider=args.provider,
                         csv_path=args.csv, from_month=args.from_month or None)
        if args.publish:
            stage = "публикация в Telegram"
            publish_to_telegram(out_path)
    except Exception as exc:
        # A --publish build is the scheduled one: tell the Dashboard chat. Only
        # the stage and exception type go out; the text can quote sheet rows.
        if not args.publish:
            raise
        traceback.print_exc()
        send_failure_alert(f"{stage}: {type(exc).__name__}")
        sys.exit(ALERT_HANDLED_EXIT_CODE)


if __name__ == "__main__":
    main()
