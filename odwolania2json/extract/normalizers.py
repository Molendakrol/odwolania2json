"""Parsowanie i normalizacja wartości: daty, kwoty, rachunki, identyfikatory, numery ogłoszeń, osoby."""

from __future__ import annotations

import re
from datetime import date

MONTHS = {
    "stycznia": 1, "lutego": 2, "marca": 3, "kwietnia": 4, "maja": 5, "czerwca": 6,
    "lipca": 7, "sierpnia": 8, "września": 9, "października": 10, "listopada": 11, "grudnia": 12,
}
_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10, "xi": 11, "xii": 12}
MONTH_RE = "|".join(MONTHS)

# Data w tekście: "3 września 2026", "03.09.2026", "3-9-2026", "2026-09-03", "3/09/2026", "3 IX 2026".
DATE_PATTERN = (
    rf"(?:\d{{1,2}}\s+(?:{MONTH_RE})\s+\d{{4}}"
    r"|\d{1,2}\s*[./-]\s*\d{1,2}\s*[./-]\s*\d{4}"
    r"|\d{4}-\d{2}-\d{2}"
    r"|\d{1,2}\s+(?:xii|xi|x|ix|viii|vii|vi|v|iv|iii|ii|i)\s+\d{4})"
)
DATE_RE = re.compile(DATE_PATTERN, re.IGNORECASE)


def parse_date(text: str | None) -> str | None:
    """Zwraca datę ISO (YYYY-MM-DD) z pierwszej rozpoznanej daty w tekście albo None."""
    if not text:
        return None
    m = DATE_RE.search(text)
    if not m:
        return None
    s = m.group(0).lower()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
            y, mo, d = map(int, s.split("-"))
        elif parts := re.fullmatch(rf"(\d{{1,2}})\s+({MONTH_RE})\s+(\d{{4}})", s):
            d, mo, y = int(parts[1]), MONTHS[parts[2]], int(parts[3])
        elif parts := re.fullmatch(r"(\d{1,2})\s+([ivx]+)\s+(\d{4})", s):
            d, mo, y = int(parts[1]), _ROMAN[parts[2]], int(parts[3])
        else:
            d, mo, y = map(int, re.split(r"\s*[./-]\s*", s))
        return date(y, mo, d).isoformat()
    except (ValueError, KeyError):
        return None


AMOUNT_RE = re.compile(r"(?<![\d.,])\d{1,3}(?:[  .]\d{3})*(?:[.,]\d{1,2})?(?![\d])|(?<![\d.,])\d+(?:[.,]\d{1,2})?(?![\d])")


def parse_amount(text: str | None) -> float | None:
    """'7 500,00 PLN' -> 7500.0; '15.000,00' -> 15000.0; 'PLN 200.00' -> 200.0."""
    if not text:
        return None
    m = AMOUNT_RE.search(text)
    if not m:
        return None
    s = m.group(0).replace(" ", "").replace(" ", "")
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    elif s.count(".") > 1 or re.search(r"\.\d{3}$", s):
        s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


ACCOUNT_RE = re.compile(r"(?<!\d)(?:PL\s?)?\d{2}(?:\s?\d{4}){6}(?!\d)", re.IGNORECASE)


def normalize_account(text: str | None) -> str | None:
    """Numer rachunku jako 26 cyfr (bez 'PL' i spacji)."""
    if not text:
        return None
    m = ACCOUNT_RE.search(text)
    if not m:
        return None
    digits = re.sub(r"\D", "", m.group(0))
    return digits if len(digits) == 26 else None


def digits_only(text: str | None, length: int | tuple[int, ...]) -> str | None:
    if not text:
        return None
    d = re.sub(r"\D", "", text)
    lengths = (length,) if isinstance(length, int) else length
    return d if len(d) in lengths else None


_TED_S = re.compile(r"(\d{4})\s*/\s*S\s*(\d{1,3})\s*[-–]\s*(\d{5,6})", re.IGNORECASE)
_TED_PLAIN = re.compile(r"(?<![\d/])(\d{5,6})\s*[-–]\s*(\d{4})(?!\d)")


def normalize_ted(text: str | None) -> str | None:
    """'2026/S 086-999073' -> '999073-2026'; '999073-2026' -> bez zmian."""
    if not text:
        return None
    if m := _TED_S.search(text):
        return f"{m[3]}-{m[1]}"
    if m := _TED_PLAIN.search(text):
        return f"{m[1]}-{m[2]}"
    return None


BZP_RE = re.compile(r"(\d{4})\s*/\s*BZP\s*(\d{8})(?:\s*/\s*(\d{1,2}))?", re.IGNORECASE)


def normalize_bzp(text: str | None) -> str | None:
    """'2025/BZP 00270698' -> '2025/BZP00270698'; korekta '/03' zachowana."""
    if not text:
        return None
    m = BZP_RE.search(text)
    if not m:
        return None
    return f"{m[1]}/BZP{m[2]}" + (f"/{m[3]}" if m[3] else "")


# Imię i nazwisko: dwa (rzadko trzy) wyrazy od wielkiej litery, nazwisko może być dwuczłonowe.
_UP = "A-ZĄĆĘŁŃÓŚŹŻ"
_LOW = "a-ząćęłńóśźż"
NAME_WORD = rf"[{_UP}][{_LOW}]+(?:-[{_UP}][{_LOW}]+)?"
PERSON_RE = re.compile(rf"\b({NAME_WORD}(?:\s+{NAME_WORD}){{1,2}})\b")
TITLE_RE = re.compile(
    r"\b(?:(?:r\.\s?pr\.|adw\.|mec\.|p\.)\s*|(?:radc[ayęą]\w*\s+prawn\w*|adwokat\w*|apl\.\s?(?:adw|radc)\w*\.?|pan[ia]?)\s+)",
    re.IGNORECASE,
)


def clean_person(text: str | None) -> str | None:
    """Usuwa tytuły zawodowe i zwraca 'Imię Nazwisko' (pierwsze dopasowanie) albo None."""
    if not text:
        return None
    m = PERSON_RE.search(TITLE_RE.sub(" | ", text))   # separator: nazwiska nie łączą się ponad usuniętym tytułem
    return m.group(1) if m else None


def collapse_ws(text: str | None) -> str | None:
    if text is None:
        return None
    out = re.sub(r"\s+", " ", text).strip()
    return out or None


EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


# ------------------------------------------------------------------------------- korekty błędów OCR
# Wyłącznie korekty KONTEKSTOWE (wzorzec + otoczenie), bez globalnych zamian znaków.

_LEGAL_FORM_OCR = re.compile(r"\b([Ss]p)\s*\.\s*[zZ]\s*([oO0])\s*\.\s*([oO0])\s*\.")


def fix_legal_form(text: str) -> str:
    """'Sp. z 0.0.' / 'Sp.Z o.0.' -> 'Sp. z o.o.' — tylko w obrębie formy prawnej spółki z o.o."""
    return _LEGAL_FORM_OCR.sub(lambda m: f"{m.group(1)}. z o.o.", text)


_LONE_PREPOSITION = re.compile(r"(?<=[a-ząćęłńóśźż] )([ZW])(?= [a-ząćęłńóśźż])")


def fix_lone_prepositions(text: str) -> str:
    """'wraz Z modernizacją' -> 'wraz z modernizacją': jednoliterowy przyimek Z/W wielką literą
    w środku zdania (słowo przed i po małą literą, w tej samej linii) to typowy błąd OCR."""
    return _LONE_PREPOSITION.sub(lambda m: m.group(1).lower(), text)


_EDGE_NOISE_LEAD = re.compile(r"^(?:[^\wąćęłńóśźżĄĆĘŁŃÓŚŹŻ„\"(]+|\w(?=\s*[^\w\s„\"(]))\s*")
_EDGE_NOISE_TRAIL = re.compile(r"(?:\s+[^\w\s)\"”]+|\s+\w|\s+\w{1,2}\s+[^\w\s]+)$")


def strip_ocr_noise(text: str | None) -> str | None:
    """Usuwa śmieci OCR z brzegów wartości: ': . Kancelaria … . e 4 :' -> 'Kancelaria …'.
    Brzeg = tokeny złożone wyłącznie z interpunkcji, pojedyncze znaki, krótkie wtrącenia przed interpunkcją."""
    if text is None:
        return None
    prev = None
    text = text.strip()
    while prev != text:
        prev = text
        text = _EDGE_NOISE_LEAD.sub("", text).strip()
        text = _EDGE_NOISE_TRAIL.sub("", text).strip()
    return text or None


_PUNCT_TOKEN = re.compile(r"(?<=\s)[:;|!¦.]+(?=\s)")


def drop_punct_tokens(text: str | None) -> str | None:
    """Usuwa samodzielne tokeny ':' ';' '|' '!' '.' (otoczone spacjami) wewnątrz tekstu (szum OCR),
    zwija spacje i ponownie koryguje przyimek Z/W, który mógł sąsiadować z usuniętym szumem."""
    if text is None:
        return None
    cleaned = collapse_ws(_PUNCT_TOKEN.sub(" ", text))
    return fix_lone_prepositions(cleaned) if cleaned else cleaned


_AT_CONFUSABLES_STRONG = "GQD(©®"
_EMAIL_REPAIR = re.compile(r"^[\w.+-]+\.[a-z]{2,}$", re.IGNORECASE)


def repair_email(token: str | None) -> str | None:
    """Naprawa adresu e-mail, w którym OCR zamienił '@' na podobny znak (G, Q, D, '(', '©', 'o').
    Stosowane tylko do tokenu stojącego po etykiecie 'e-mail:'. Zamiana tylko, gdy kandydat jest jednoznaczny."""
    if not token:
        return None
    token = token.strip().strip(".,;:")
    if "@" in token:
        m = EMAIL_RE.search(token)
        return m.group(0).lower() if m else None
    if not _EMAIL_REPAIR.match(token.replace("(", "x").replace("©", "x").replace("®", "x")):
        return None
    body, _, _tld = token.rpartition(".")
    strong = [i for i, ch in enumerate(body) if ch in _AT_CONFUSABLES_STRONG and 0 < i < len(body) - 1]
    weak = [i for i, ch in enumerate(body) if ch == "o" and 0 < i < len(body) - 1]
    if len(strong) == 1:
        i = strong[0]
    elif not strong and len(weak) == 1:
        i = weak[0]
    else:
        return None
    candidate = token[:i] + "@" + token[i + 1:]
    m = EMAIL_RE.fullmatch(candidate)
    return candidate.lower() if m and "." in candidate.split("@")[1] else None
