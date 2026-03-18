"""
Language detector for L1 input gateway.

Supports only: en, zh-cn, zh-tw, yue (heuristic).

Design notes
============
langdetect is unreliable on short texts (< ~20 chars).  A pure token of "hi",
"ok", "yes", "bye" etc. is regularly classified as Swahili, Slovak, Turkish,
Norwegian — which would silently block real users.

We therefore apply three fast-path gates **before** calling langdetect:

  1. Script gate  — Kana / Hangul / Cyrillic → immediately unsupported.
  2. CJK gate     — any Han character → supported (zh-tw/yue).
  3. Short-text gate — inputs ≤ SHORT_TEXT_THRESHOLD chars that contain only
     ASCII / basic Latin are passed through as "en" (benefit of the doubt).
     Rationale: a mental-health chatbot user typing "hi", "ok" or ":(  " should
     never be blocked.  Malicious payloads in Latin-only scripts that are longer
     than the threshold are still processed by langdetect and can be blocked.

The English lexical hint path is kept as an additional signal for medium-length
texts that share vocabulary with our service domain.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from langdetect import DetectorFactory, LangDetectException, detect_langs

# Keep detection deterministic across runs.
DetectorFactory.seed = 0

ALLOWED_LANGUAGES = {"en", "zh-cn", "zh-tw", "yue"}

UNSUPPORTED_LANGUAGE_MESSAGE = (
    "很抱歉，阿本目前還在學習中，暫時只能用中文、粵語或英文與您交流喔！"
    "如果需要協助，請嘗試用這些語言跟我說。"
)

# ── Script / character-class patterns ───────────────────────────────────────
_JAPANESE_KANA = re.compile(r"[\u3040-\u30ff]")
_HANGUL        = re.compile(r"[\uac00-\ud7af]")
_CYRILLIC      = re.compile(r"[\u0400-\u04ff]")
_HAN           = re.compile(r"[\u4e00-\u9fff]")
_ASCII_ONLY    = re.compile(r"^[\x00-\x7f]+$")   # pure ASCII (includes basic Latin)
_EN_TOKEN      = re.compile(r"[a-z']+")

# ── Cantonese heuristic ──────────────────────────────────────────────────────
_YUE_PATTERN = re.compile(
    r"(?:唔|喺|佢|咩|乜|嘅|咗|哋|呢|咁|噉|冇|而家|邊個|點解|點樣)"
)

# ── English lexical hints (common mental-health / service vocabulary) ────────
_EN_HINT_WORDS = {
    "i", "im", "i'm", "ive", "i've",
    "me", "my", "myself",
    "feel", "feeling", "felt",
    "want", "help", "please",
    "sad", "stress", "anxious", "anxiety",
    "life", "kill", "hurt", "harm", "suicidal", "suicide",
    "self", "been", "have", "am", "is", "are",
    "the", "a", "an", "and", "or", "but",
    "hi", "hello", "hey", "ok", "okay", "yes", "no",
    "bye", "thanks", "thank",
}

# Short texts (≤ this many non-whitespace chars) that are pure-ASCII are treated
# as English to avoid langdetect misclassification of common short words.
# 15 covers: hi/ok/yes/no/bye/okay/sure/lol/:(  and similar single-word replies.
# Anything ≥ 16 visible chars goes through langdetect, which correctly classifies
# French/German/Spanish sentences and blocks them.
SHORT_TEXT_THRESHOLD = 15


@dataclass
class LanguageDetection:
    language: str
    supported: bool
    confidence: float


def get_unsupported_language_message() -> str:
    return UNSUPPORTED_LANGUAGE_MESSAGE


def _contains_unsupported_script(text: str) -> str | None:
    """Return script name if an explicitly unsupported script is found."""
    if _JAPANESE_KANA.search(text):
        return "ja"
    if _HANGUL.search(text):
        return "ko"
    if _CYRILLIC.search(text):
        return "cyrillic"
    return None


def _looks_like_english(text: str) -> bool:
    """
    Lexical-hint check for medium-length texts.
    Returns True only when a meaningful share of tokens belong to our English
    vocabulary list — avoids false-positives on other Latin-script languages.
    """
    tokens = _EN_TOKEN.findall(text.lower())
    if not tokens:
        return False
    hits = sum(1 for t in tokens if t in _EN_HINT_WORDS)
    if hits == 0:
        return False
    return hits / len(tokens) >= 0.15


def detect_language(text: str) -> LanguageDetection:
    """
    Detect language and return whether it is supported by product policy.

    Fast-path order (cheapest checks first):
      1. Empty input → unknown / unsupported
      2. Unsupported script (Kana / Hangul / Cyrillic) → unsupported
      3. CJK Han characters → zh-tw / supported
      4. Cantonese particles → yue / supported
      5. Short pure-ASCII text → en / supported  ← prevents langdetect FP
      6. English lexical hints → en / supported
      7. langdetect fallback
    """
    sample = text.strip()
    if not sample:
        return LanguageDetection(language="unknown", supported=False, confidence=0.0)

    # ── 1. Unsupported script gate ───────────────────────────────────────────
    script_lang = _contains_unsupported_script(sample)
    if script_lang:
        return LanguageDetection(language=script_lang, supported=False, confidence=1.0)

    # ── 2. CJK Han gate ──────────────────────────────────────────────────────
    if _HAN.search(sample):
        # Cantonese particles narrow it down further, but both map to supported.
        lang = "yue" if _YUE_PATTERN.search(sample) else "zh-tw"
        return LanguageDetection(language=lang, supported=True, confidence=0.9)

    # ── 3. Short pure-ASCII gate (benefit of the doubt = English) ────────────
    visible = sample.replace(" ", "").replace("\n", "").replace("\t", "")
    if len(visible) <= SHORT_TEXT_THRESHOLD and _ASCII_ONLY.match(sample):
        return LanguageDetection(language="en", supported=True, confidence=0.8)

    # ── 4. English lexical-hint gate ─────────────────────────────────────────
    if _looks_like_english(sample):
        return LanguageDetection(language="en", supported=True, confidence=0.85)

    # ── 5. langdetect fallback ───────────────────────────────────────────────
    try:
        detected = detect_langs(sample)
    except LangDetectException:
        # Cannot determine language → treat as unsupported (conservative)
        return LanguageDetection(language="unknown", supported=False, confidence=0.0)

    if not detected:
        return LanguageDetection(language="unknown", supported=False, confidence=0.0)

    top = detected[0]
    lang = top.lang.lower()
    confidence = float(top.prob)

    # langdetect may return "zh" for Chinese depending on input length.
    if lang == "zh":
        lang = "zh-tw"

    return LanguageDetection(
        language=lang,
        supported=lang in ALLOWED_LANGUAGES,
        confidence=confidence,
    )
