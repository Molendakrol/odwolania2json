"""Normalizacja tekstu wyciągniętego z PDF.

Dwa poziomy:
- ``clean_text`` — tekst „do czytania”: NFC, ujednolicone spacje, zachowane podziały linii.
- ``matching_text`` / ``fold_diacritics`` — tekst „do dopasowań”: małe litery, scalone
  rozstrzelone słowa, zwinięte białe znaki; opcjonalnie bez polskich znaków (odporność na OCR).
"""

from __future__ import annotations

import re
import unicodedata

_SPACE_CHARS = re.compile(r"[  -​  　\t\f\v]")
_SOFT_HYPHEN = "­"
_MULTI_SPACE = re.compile(r" {2,}")
_MULTI_BLANK_LINES = re.compile(r"\n{3,}")
# Litery rozstrzelone: "P E Ł N O M O C N I C T W O" (co najmniej 4 pojedyncze litery).
_LETTER_SPACED = re.compile(r"(?<!\w)(?:\w ){3,}\w(?!\w)")
# Dywiz na końcu linii przed małą literą: "wdro-\nżenie" -> "wdrożenie".
_HYPHEN_BREAK = re.compile(r"(\w)-\n(?=[a-ząćęłńóśźż])")

_POLISH_FOLD = str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "acelnoszzACELNOSZZ")


def clean_text(raw: str) -> str:
    """Tekst czytelny: NFC, bez znaków sterujących, pojedyncze spacje, max 1 pusta linia."""
    text = unicodedata.normalize("NFC", raw or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace(_SOFT_HYPHEN, "")
    text = _SPACE_CHARS.sub(" ", text)
    text = "".join(ch for ch in text if ch == "\n" or unicodedata.category(ch)[0] != "C")
    lines = [_MULTI_SPACE.sub(" ", line).strip() for line in text.split("\n")]
    text = "\n".join(lines)
    return _MULTI_BLANK_LINES.sub("\n\n", text).strip()


def _join_letter_spaced(match: re.Match[str]) -> str:
    return match.group(0).replace(" ", "")


def matching_text(clean: str) -> str:
    """Tekst do dopasowań wzorców: małe litery, scalone rozstrzelone słowa,
    połączone przeniesienia wyrazów; podziały linii zachowane (wzorce strukturalne)."""
    text = _HYPHEN_BREAK.sub(r"\1", clean)
    text = "\n".join(_LETTER_SPACED.sub(_join_letter_spaced, line) for line in text.split("\n"))
    return text.lower()


def fold_diacritics(text: str) -> str:
    """Usuwa polskie znaki diakrytyczne (ł->l itd.) — wariant odporny na błędy OCR."""
    text = text.translate(_POLISH_FOLD)
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def count_meaningful_chars(text: str) -> int:
    """Liczba znaków alfanumerycznych — miara „ilości treści” niezależna od białych znaków."""
    return sum(1 for ch in text if ch.isalnum())
