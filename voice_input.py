#!/usr/bin/env python3
"""Voice expense input: offline speech-to-text + Claude structured extraction.

Pipeline (no paid speech API):
  1. Telegram voice arrives as OGG/Opus bytes.
  2. Decode to 16 kHz mono PCM with PyAV (bundles ffmpeg libs — no system ffmpeg).
  3. Transcribe locally with a small Vosk Russian model (downloaded on first use).
  4. Ask Claude to turn the free-text transcript into a strict list of expenses,
     constrained to the sheet's allowed categories and currencies.

The Vosk model can be mediocre on free speech, so the confirmation card in the
bot lets the user fix category/date/currency before saving.
"""
from __future__ import annotations

import io
import json
import os
import urllib.request
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

BOT_DIR = Path(__file__).resolve().parent
MODELS_DIR = BOT_DIR / "models"

# Small Russian model (~45 MB): good enough for short expense phrases, light on
# RAM. Override the whole path with VOSK_MODEL_PATH if you ship a bigger model.
DEFAULT_MODEL_NAME = "vosk-model-small-ru-0.22"
MODEL_URL = f"https://alphacephei.com/vosk/models/{DEFAULT_MODEL_NAME}.zip"

SAMPLE_RATE = 16000
DEFAULT_LLM_MODEL = os.getenv("VX_LLM_MODEL", "claude-sonnet-5")

# Known merchants/brands → category hints for the LLM. Vosk often mangles brand
# names (especially Latin ones like "DM"), so we also list phonetic ASR variants.
# When Claude sees any of these in the transcript it maps the expense to the given
# category and writes a clean merchant name into the comment.
# Extend this list whenever you notice a store being misclassified.
#   name     — canonical merchant name to put in the comment
#   aliases  — spellings/phonetic variants Vosk may produce
#   category — target category (should exist in the Config category list)
#   note     — optional human hint appended to the prompt line
MERCHANT_HINTS: list[dict] = [
    {"name": "DM", "aliases": ["dm", "дм", "де эм", "дэ эм", "деэм", "дэ-эм", "деем"],
     "category": "Household", "note": "дрогери-маркет (Drogerie Markt), бытовая химия/косметика"},
]

# Lazily-loaded Vosk model (loading is slow and memory-heavy — do it once).
_model = None


# ---------------------------------------------------------------------------
# Audio decode (OGG/Opus -> 16 kHz mono PCM), no system ffmpeg required
# ---------------------------------------------------------------------------
def decode_to_pcm16k_mono(data: bytes) -> bytes:
    """Decode arbitrary audio bytes to raw 16-bit mono PCM at 16 kHz."""
    import av  # PyAV wheels bundle ffmpeg libraries

    container = av.open(io.BytesIO(data))
    resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
    chunks: list[bytes] = []

    def _emit(frame) -> None:
        # AudioResampler.resample returns a list on modern PyAV, a single frame
        # on older versions; normalize to a list.
        out = resampler.resample(frame)
        frames = out if isinstance(out, list) else [out]
        for f in frames:
            if f is not None:
                chunks.append(f.to_ndarray().tobytes())

    for frame in container.decode(audio=0):
        _emit(frame)
    _emit(None)  # flush the resampler
    container.close()
    return b"".join(chunks)


# ---------------------------------------------------------------------------
# Speech-to-text (Vosk, offline)
# ---------------------------------------------------------------------------
def ensure_model() -> Path:
    """Return the local Vosk model path, downloading it once if missing."""
    override = os.getenv("VOSK_MODEL_PATH")
    if override:
        return Path(override)
    path = MODELS_DIR / DEFAULT_MODEL_NAME
    if path.exists():
        return path
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = MODELS_DIR / f"{DEFAULT_MODEL_NAME}.zip"
    print(f"→ Downloading Vosk model {DEFAULT_MODEL_NAME} (one-time)…")
    urllib.request.urlretrieve(MODEL_URL, zip_path)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(MODELS_DIR)
    zip_path.unlink(missing_ok=True)
    return path


def get_model():
    global _model
    if _model is None:
        from vosk import Model
        _model = Model(str(ensure_model()))
    return _model


def transcribe(data: bytes) -> str:
    """Transcribe audio bytes (OGG/Opus, etc.) to text using Vosk."""
    from vosk import KaldiRecognizer

    pcm = decode_to_pcm16k_mono(data)
    rec = KaldiRecognizer(get_model(), SAMPLE_RATE)
    rec.AcceptWaveform(pcm)
    result = json.loads(rec.FinalResult())
    return (result.get("text") or "").strip()


# ---------------------------------------------------------------------------
# Structured extraction (Claude): transcript -> list of expenses
# ---------------------------------------------------------------------------
def _extract_json_object(text: str) -> dict:
    """Parse a JSON object from a model reply, tolerating ```json fences."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1] if "\n" in text else text
        text = text.rsplit("```", 1)[0].strip()
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start:end + 1]
    return json.loads(text)


def _merchant_hints_block() -> str:
    """Render the MERCHANT_HINTS table as a prompt section (empty if none)."""
    if not MERCHANT_HINTS:
        return ""
    lines = []
    for m in MERCHANT_HINTS:
        aliases = ", ".join(m.get("aliases", []))
        note = f" — {m['note']}" if m.get("note") else ""
        alias_part = f" (в речи может звучать как: {aliases})" if aliases else ""
        lines.append(f"- «{m['name']}»{alias_part} → категория «{m['category']}»{note}")
    return (
        "Известные магазины/бренды. Если в тексте встречается такое слово или "
        "что-то похожее по звучанию — это магазин: поставь указанную категорию, а в "
        "comment запиши каноническое название магазина:\n" + "\n".join(lines) + "\n\n"
    )


def _build_prompt(transcript: str, categories: list[str], currencies: list[str],
                  today: str, who: str, default_currency: str) -> str:
    cats = "\n".join(f"- {c}" for c in categories)
    curs = ", ".join(currencies)
    return (
        "Ты парсер расходов из распознанной (возможно, с ошибками) русской речи. "
        "Извлеки из текста ОДНУ или НЕСКОЛЬКО трат и верни СТРОГО JSON.\n\n"
        f"Сегодня: {today} (формат ДД.ММ.ГГГГ). Пользователь: {who}.\n\n"
        f"Допустимые КАТЕГОРИИ (выбери ровно одну для каждой траты):\n{cats}\n\n"
        f"Допустимые ВАЛЮТЫ (символы): {curs}. Валюта по умолчанию, если не "
        f"названа: {default_currency}.\n\n"
        + _merchant_hints_block() +
        "Правила:\n"
        "- Числа могут быть словами («сто», «двести пятьдесят») — переведи в число.\n"
        "- Слова валют: рубль/рублей→₽, динар/динаров→дин, евро→€, иена/йен→¥, "
        "доллар→$. Если валюта не названа — используй валюту по умолчанию.\n"
        "- Дата: относительные слова («сегодня», «вчера», «позавчера») и явные "
        "(«восьмое июля», «8 июля») переведи в ДД.ММ.ГГГГ, отсчитывая от сегодня. "
        "Если дата не названа — сегодня.\n"
        "- comment: короткое описание товара/траты в оригинале (например «чипсы», "
        "«продукты из Wolt»). Без суммы и валюты внутри.\n"
        "- category: строго одна из списка. Если ничего не подходит — 'Other'.\n"
        "- Если сумму понять нельзя — НЕ включай такую трату.\n\n"
        "Формат ответа (без markdown):\n"
        '{"expenses":[{"comment":"...","amount":123.45,"currency":"дин",'
        '"date":"ДД.ММ.ГГГГ","category":"..."}],"note":"кратко, если что-то неясно"}\n\n'
        f"Текст: {transcript}"
    )


def _snap_category(value: Optional[str], categories: list[str]) -> str:
    if value:
        low = value.strip().lower()
        for c in categories:
            if c.lower() == low:
                return c
        for c in categories:
            if low and (low in c.lower() or c.lower() in low):
                return c
    for c in categories:
        if c.lower() == "other":
            return c
    return categories[-1] if categories else "Other"


def _snap_currency(value: Optional[str], currencies: list[str],
                   default_currency: str) -> str:
    if value and value.strip() in currencies:
        return value.strip()
    return default_currency


def _snap_date(value: Optional[str], today: str) -> str:
    if value:
        try:
            return datetime.strptime(value.strip(), "%d.%m.%Y").strftime("%d.%m.%Y")
        except ValueError:
            pass
    return today


def _snap_amount(value) -> Optional[float]:
    try:
        return round(float(str(value).replace(",", ".").strip()), 2)
    except (TypeError, ValueError):
        return None


def extract_expenses(transcript: str, categories: list[str], currencies: list[str],
                     today: str, who: str,
                     default_currency: str = "дин",
                     model: str = DEFAULT_LLM_MODEL) -> dict:
    """Turn a transcript into {'expenses': [...], 'note': str} via Claude.

    Each expense is snapped to the allowed categories/currencies and a valid
    date. Items without a parseable amount are dropped (and flagged in 'note').
    Requires ANTHROPIC_API_KEY in the environment.
    """
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is not set — cannot parse the expense.")

    import anthropic

    client = anthropic.Anthropic()
    prompt = _build_prompt(transcript, categories, currencies, today, who, default_currency)
    resp = client.messages.create(
        model=model,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(block.text for block in resp.content if block.type == "text")
    parsed = _extract_json_object(text)

    raw_items = parsed.get("expenses") or []
    note = (parsed.get("note") or "").strip()
    items: list[dict] = []
    dropped = 0
    for it in raw_items:
        amount = _snap_amount(it.get("amount"))
        if amount is None:
            dropped += 1
            continue
        items.append({
            "comment": (it.get("comment") or "").strip(),
            "amount": amount,
            "currency": _snap_currency(it.get("currency"), currencies, default_currency),
            "date": _snap_date(it.get("date"), today),
            "category": _snap_category(it.get("category"), categories),
        })
    if dropped:
        extra = f"пропущено трат без суммы: {dropped}"
        note = f"{note}; {extra}" if note else extra
    return {"expenses": items, "note": note}
