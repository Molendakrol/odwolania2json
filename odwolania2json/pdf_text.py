"""Ekstrakcja tekstu z PDF wraz z diagnostyką; OCR jako fallback.

Kolejność:
1. Tekst z warstwy tekstowej PDF (native) dla wszystkich stron.
2. OCR tylko gdy jest potrzebny (i silnik OCR jest dostępny):
   - `no_text`  — dokument nie ma użytecznej warstwy tekstowej (np. skan),
   - `low_text` — tekstu jest za mało,
   - `scanned_pages` — dokument ma tekst, ale część stron to obrazy bez tekstu (dokument mieszany).
   OCR obejmuje wyłącznie strony bez wystarczającego tekstu native; strony z tekstem native zostają bez zmian.
3. Statystyki i status liczone na tekście końcowym; źródło tekstu: native / ocr / mixed / none.
Błąd OCR nie przerywa przetwarzania — jest zapisywany w diagnostyce, a dokument zachowuje tekst native.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

import pymupdf

from .normalize import clean_text, count_meaningful_chars

if TYPE_CHECKING:
    from .ocr import OcrEngine

# Progi jakości tekstu (znaki alfanumeryczne).
MIN_CHARS_DOCUMENT = 200   # poniżej: dokument traktowany jako „bez tekstu”
MIN_CHARS_PAGE = 30        # poniżej: strona traktowana jako pusta (np. skan)


class ExtractionStatus(str, Enum):
    OK = "ok"                # wystarczająco dużo tekstu
    LOW_TEXT = "low_text"    # jest trochę tekstu, ale poniżej progu dokumentu
    NO_TEXT = "no_text"      # brak tekstu (także po OCR / gdy OCR niedostępny)
    ERROR = "error"          # plik nieczytelny / uszkodzony / zaszyfrowany


class TextSource(str, Enum):
    NATIVE = "native"        # tekst z warstwy tekstowej PDF
    OCR = "ocr"              # tekst wyłącznie z OCR
    MIXED = "mixed"          # część stron native, część z OCR
    NONE = "none"            # nie uzyskano tekstu


@dataclass
class OcrInfo:
    attempted: bool = False
    reason: str | None = None            # no_text / low_text / scanned_pages
    status: str = "not_needed"           # not_needed / disabled / unavailable / ok / partial / failed
    engine: str | None = None
    language: str | None = None
    dpi: int | None = None
    pages_requested: list[int] = field(default_factory=list)    # numery stron (od 1)
    pages_processed: int = 0
    pages_with_text: int = 0
    seconds: float = 0.0
    errors: list[str] = field(default_factory=list)


@dataclass
class ExtractionResult:
    path: str
    status: ExtractionStatus
    page_count: int = 0
    char_count: int = 0                 # znaki tekstu po czyszczeniu
    meaningful_chars: int = 0           # znaki alfanumeryczne
    pages_with_text: int = 0
    pages: list[str] = field(default_factory=list)   # oczyszczony tekst per strona (kolejność stron zachowana)
    page_sources: list[str] = field(default_factory=list)   # per strona: native / ocr / none
    text_source: TextSource = TextSource.NONE
    native_status: ExtractionStatus | None = None    # status przed OCR
    ocr: OcrInfo = field(default_factory=OcrInfo)
    error: str | None = None

    @property
    def text(self) -> str:
        return "\n\n".join(self.pages)

    @property
    def has_sufficient_text(self) -> bool:
        return self.status is ExtractionStatus.OK

    def diagnostics(self) -> dict:
        """Diagnostyka bez pełnego tekstu (do logów i raportów)."""
        data = asdict(self)
        data.pop("pages")
        data["status"] = self.status.value
        data["text_source"] = self.text_source.value
        data["native_status"] = self.native_status.value if self.native_status else None
        data["has_sufficient_text"] = self.has_sufficient_text
        return data


def _status_for(meaningful: int, page_count: int) -> ExtractionStatus:
    if page_count == 0 or meaningful == 0:
        return ExtractionStatus.NO_TEXT
    if meaningful < MIN_CHARS_DOCUMENT:
        return ExtractionStatus.LOW_TEXT
    return ExtractionStatus.OK


def _ocr_plan(status: ExtractionStatus, page_chars: list[int], page_has_images: list[bool]) -> tuple[str | None, list[int]]:
    """(powód OCR, indeksy stron do OCR) albo (None, [])."""
    low_pages = [i for i, c in enumerate(page_chars) if c < MIN_CHARS_PAGE]
    if status is ExtractionStatus.NO_TEXT:
        return "no_text", low_pages
    if status is ExtractionStatus.LOW_TEXT:
        return "low_text", [i for i, c in enumerate(page_chars) if c < MIN_CHARS_PAGE or page_has_images[i]]
    scanned = [i for i in low_pages if page_has_images[i]]
    return ("scanned_pages", scanned) if scanned else (None, [])


def _run_ocr(doc: pymupdf.Document, result: ExtractionResult, pages: list[int], engine: "OcrEngine") -> None:
    info = result.ocr
    info.engine, info.language, info.dpi = engine.name, engine.language, engine.dpi
    available, detail = engine.is_available()
    if not available:
        info.status = "unavailable"
        info.errors.append(detail)
        return
    info.attempted = True
    started = time.perf_counter()
    for i in pages:
        try:
            text = clean_text(engine.ocr_page(doc[i]))
        except Exception as exc:  # błąd renderowania / OCR jednej strony nie zatrzymuje dokumentu
            info.errors.append(f"strona {i + 1}: {type(exc).__name__}: {exc}")
            continue
        info.pages_processed += 1
        if count_meaningful_chars(text) >= MIN_CHARS_PAGE:
            info.pages_with_text += 1
        if count_meaningful_chars(text) > count_meaningful_chars(result.pages[i]):
            result.pages[i] = text
            result.page_sources[i] = "ocr"
    info.seconds = round(time.perf_counter() - started, 3)
    if info.pages_processed == 0:
        info.status = "failed"
    elif info.errors:
        info.status = "partial"
    else:
        info.status = "ok"


def extract_text(path: str | Path, ocr: "OcrEngine | None" = None) -> ExtractionResult:
    """Wyciąga tekst z PDF (kolejność strumienia treści), z OCR jako fallbackiem, gdy podano silnik.

    Bez sortowania geometrycznego: sortowanie sklejało kolumny ułożone obok siebie w jedną linię
    (np. „Mocodawca: Pełnomocnictwo przyjmuję:”), co psuło układy etykieta/wartość.
    """
    path = Path(path)
    result = ExtractionResult(path=str(path), status=ExtractionStatus.ERROR)
    try:
        doc = pymupdf.open(path, filetype="pdf")
    except Exception as exc:  # uszkodzony plik, nie-PDF, brak pliku
        result.error = f"{type(exc).__name__}: {exc}"
        return result

    with doc:
        if doc.needs_pass:
            result.error = "PDF zaszyfrowany hasłem"
            return result
        result.page_count = doc.page_count
        page_has_images: list[bool] = []
        try:
            for page in doc:
                page_text = clean_text(page.get_text("text"))
                result.pages.append(page_text)
                page_has_images.append(bool(page.get_images(full=False)))
        except Exception as exc:
            result.error = f"{type(exc).__name__}: {exc}"
            return result

        page_chars = [count_meaningful_chars(t) for t in result.pages]
        result.page_sources = ["native" if c > 0 else "none" for c in page_chars]
        result.native_status = _status_for(sum(page_chars), result.page_count)

        reason, to_ocr = _ocr_plan(result.native_status, page_chars, page_has_images)
        if reason and to_ocr:
            result.ocr.reason = reason
            result.ocr.pages_requested = [i + 1 for i in to_ocr]
            if ocr is None:
                result.ocr.status = "disabled"
            else:
                _run_ocr(doc, result, to_ocr, ocr)

    text = result.text
    result.char_count = len(text)
    result.meaningful_chars = count_meaningful_chars(text)
    result.pages_with_text = sum(1 for t in result.pages if count_meaningful_chars(t) >= MIN_CHARS_PAGE)
    result.status = _status_for(result.meaningful_chars, result.page_count)
    used = {s for s, t in zip(result.page_sources, result.pages) if count_meaningful_chars(t) > 0}
    if not used or used == {"none"}:
        result.text_source = TextSource.NONE
    elif used == {"native"}:
        result.text_source = TextSource.NATIVE
    elif used == {"ocr"}:
        result.text_source = TextSource.OCR
    else:
        result.text_source = TextSource.MIXED
    return result
