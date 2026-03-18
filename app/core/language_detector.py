"""
Language detector for L1 input gateway.

Supports only: en, zh-cn, zh-tw, yue (heuristic).
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

# Common Cantonese particles and words (heuristic only).
_YUE_PATTERN = re.compile(
    r"(?:唔|喺|佢|咩|乜|嘅|咗|哋|呢|咁|噉|冇|而家|邊個|點解|點樣)"
)

_JAPANESE_KANA = re.compile(r"[\u3040-\u30ff]")
_HANGUL = re.compile(r"[\uac00-\ud7af]")
_CYRILLIC = re.compile(r"[\u0400-\u04ff]")
_HAN = re.compile(r"[\u4e00-\u9fff]")
_EN_TOKEN = re.compile(r"[a-z']+")

_EN_HINT_WORDS = {
    "i",
    "im",
    "i'm",
    "ive",
    "i've",
    "me",
    "my",
    "myself",
    "feel",
    "want",
    "help",
    "sad",
    "stress",
    "anxious",
    "anxiety",
    "life",
    "kill",
    "hurt",
    "harm",
    "suicidal",
    "suicide",
    "self",
    "been",
}


@dataclass
class LanguageDetection:
    language: str
    supported: bool
    confidence: float


def get_unsupported_language_message() -> str:
    return UNSUPPORTED_LANGUAGE_MESSAGE


def _contains_unsupported_script(text: str) -> str | None:
    if _JAPANESE_KANA.search(text):
        return "ja"
    if _HANGUL.search(text):
        return "ko"
    if _CYRILLIC.search(text):
        return "cyrillic"
    return None


def _looks_like_english(text: str) -> bool:
    tokens = _EN_TOKEN.findall(text.lower())
    if not tokens:
        return False

    hits = sum(1 for token in tokens if token in _EN_HINT_WORDS)
    if hits == 0:
        return False

    # A tiny threshold keeps it fast while avoiding broad latin-language allow.
    return hits / len(tokens) >= 0.15


def detect_language(text: str) -> LanguageDetection:
    """Detect language and whether it is supported by product policy."""
    sample = text.strip()
    if not sample:
        return LanguageDetection(language="unknown", supported=False, confidence=0.0)

    # Fast path: unsupported scripts.
    script_lang = _contains_unsupported_script(sample)
    if script_lang:
        return LanguageDetection(language=script_lang, supported=False, confidence=1.0)

    # Fast path: CJK Han text (Traditional/Simplified Chinese share Han script).
    if _HAN.search(sample):
        return LanguageDetection(language="zh-tw", supported=True, confidence=0.9)

    # Cantonese heuristic path.
    if _YUE_PATTERN.search(sample):
        return LanguageDetection(language="yue", supported=True, confidence=0.9)

    # English lexical hint path.
    if _looks_like_english(sample):
        return LanguageDetection(language="en", supported=True, confidence=0.85)

    try:
        detected = detect_langs(sample)
    except LangDetectException:
        return LanguageDetection(language="unknown", supported=False, confidence=0.0)

    if not detected:
        return LanguageDetection(language="unknown", supported=False, confidence=0.0)

    top = detected[0]
    lang = top.lang.lower()
    confidence = float(top.prob)

    # langdetect may return zh for Chinese text depending on input length.
    if lang == "zh":
        lang = "zh-tw"

    return LanguageDetection(
        language=lang,
        supported=lang in ALLOWED_LANGUAGES,
        confidence=confidence,
    )
