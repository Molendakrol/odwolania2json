"""Wspólne wyszukiwanie podmiotów (nazwa + KRS/NIP/REGON) w tekście dokumentu."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .normalizers import digits_only, strip_ocr_noise

# Formy prawne kończące nazwę podmiotu.
LEGAL_FORM = (
    r"(?:sp\.\s?z\s?o\.\s?o\.(?:\s?sp\.\s?k\.)?|spółka\s+z\s+ograniczon[aą]\s+odpowiedzialności[aą](?:\s+sp\.\s?k\.)?"
    r"|spółka\s+komandytowa|spółka\s+jawna|spółka\s+akcyjna|s\.\s?a\.|p\.\s?s\.\s?a\.|sp\.\s?j\.|sp\.\s?k\.|sp\.\s?p\."
    r"|s\.\s?c\.|spółka\s+partnerska)"
)
_ID_RE = re.compile(r"\b(KRS|NIP|REGON)\b\s*(?:nr\s*)?[:.]?\s*(\d[\d\s-]{7,16}\d)", re.IGNORECASE)
ADDRESS_START = re.compile(r"(?:,\s*|\s)(?:ul\.|al\.|pl\.|os\.|ulica\b|\d{2}-\d{3}\b|z\s+siedzibą\b)", re.IGNORECASE)
NAME_WITH_FORM = re.compile(
    rf"[A-ZĄĆĘŁŃÓŚŹŻ][^,;:()\n„”\"§]{{0,120}}?\b(?i:{LEGAL_FORM})(?![\wąćęłńóśźż])"
)
_PREFIX = re.compile(
    r"^(?:(?:(?:(?i:pełnomocnictwo)(?:\s+(?i:procesowe))?|PEŁNOMOCNI\w*|MOCODAWCA|Mocodawc[aą]\s+jest|Mocodawca|jest|"
    r"Dane\s+rejestrowe|Działając\s+w\s+imieniu|Lider\w*(?:\s+Konsorcjum)?|Partner\w*(?:\s+Konsorcjum)?|"
    r"Wykonawc\w*|Odwołując\w*|Zamawiając\w*|W\s+imieniu|Za|reprezentacji|Konsorcjum)(?![\wąćęłńóśźż])"
    r"|\d+[).]|[-–•§])\s*:?\s*)+"
)


@dataclass
class Entity:
    name: str | None
    krs: str | None
    nip: str | None
    regon: str | None
    start: int
    end: int


def clean_name(name: str | None) -> str | None:
    if not name:
        return None
    name = strip_ocr_noise(re.sub(r"\s+", " ", name)) or ""
    name = name.strip(" ,;:-–\"„”")
    name = re.sub(r"^(?:\w ){3,}\w(?!\w)\s*", "", name)  # rozstrzelony nagłówek, np. "P E Ł N O M O C N I C T W O"
    name = _PREFIX.sub("", name).strip(" ,;:-–\"„”")
    if name and not (name[0].isupper() or name[0].isdigit()):
        return None   # np. „odpowiedzialnością …” — urwany fragment nazwy, nie nazwa
    return name or None


def find_ids(text: str, start: int = 0, end: int | None = None) -> dict[str, str]:
    """Pierwsze wystąpienie KRS/NIP/REGON w zakresie tekstu."""
    found: dict[str, str] = {}
    for m in _ID_RE.finditer(text, start, len(text) if end is None else end):
        kind = m.group(1).upper()
        length = {"KRS": 10, "NIP": 10, "REGON": (9, 14)}[kind]
        value = digits_only(m.group(2), length)
        if value and kind not in found:
            found[kind] = value
    return found


def id_clusters(text: str) -> list[tuple[int, int, dict[str, str]]]:
    """Grupuje kolejne identyfikatory w klastry podmiotów (nowy NIP/KRS już obecny w klastrze = nowy podmiot)."""
    clusters: list[tuple[int, int, dict[str, str]]] = []
    for m in _ID_RE.finditer(text):
        kind = m.group(1).upper()
        length = {"KRS": 10, "NIP": 10, "REGON": (9, 14)}[kind]
        value = digits_only(m.group(2), length)
        if not value:
            continue
        if clusters and kind not in clusters[-1][2] and m.start() - clusters[-1][1] < 150:
            s, _, ids = clusters[-1]
            ids[kind] = value
            clusters[-1] = (s, m.end(), ids)
        else:
            clusters.append((m.start(), m.end(), {kind: value}))
    return clusters


def name_before(text: str, pos: int, floor: int = 0) -> str | None:
    """Nazwa podmiotu kończąca się przed `pos` (szuka formy prawnej; bez niej — fragment przed adresem)."""
    window = text[max(floor, pos - 400):pos]
    matches = list(NAME_WITH_FORM.finditer(window))
    if matches:
        return clean_name(matches[-1].group(0))
    addr = list(ADDRESS_START.finditer(window))
    if addr:
        before = window[: addr[-1].start()]
        seg = re.split(r"[;:\n]|\b\d\)\s", before)[-1]
        return clean_name(seg)
    return None
