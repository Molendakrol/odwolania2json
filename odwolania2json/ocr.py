"""OCR stron PDF jako fallback ekstrakcji tekstu.

Silnik: Tesseract wbudowany w PyMuPDF (MuPDF) — bez dodatkowych pakietów Pythona i bez programu
`tesseract` w systemie. Wymagany jest jedynie plik danych języka (`pol.traineddata`) w katalogu tessdata.
Strona jest renderowana lokalnie do obrazu i rozpoznawana na CPU; dokument nie opuszcza komputera.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import pymupdf

PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_CONFIG = PROJECT_ROOT / "config" / "app.toml"
BUNDLED_TESSDATA = PROJECT_ROOT / "ocr" / "tessdata"


class OcrEngine(Protocol):
    name: str
    language: str
    dpi: int

    def is_available(self) -> tuple[bool, str]:
        """(czy silnik może działać, opis/powód)."""
        ...

    def ocr_page(self, page: pymupdf.Page) -> str:
        """Tekst rozpoznany ze strony. Może zgłosić wyjątek — obsługuje go wywołujący."""
        ...


@dataclass
class OcrSettings:
    enabled: bool = True
    language: str = "pol"
    dpi: int = 300
    tessdata_dir: str = ""      # pusty = wyszukiwanie automatyczne


def load_ocr_settings(path: Path = APP_CONFIG) -> OcrSettings:
    with open(path, "rb") as fh:
        cfg = tomllib.load(fh).get("ocr", {})
    settings = OcrSettings(
        enabled=bool(cfg.get("enabled", True)),
        language=str(cfg.get("language", "pol")),
        dpi=int(cfg.get("dpi", 300)),
        tessdata_dir=str(cfg.get("tessdata_dir", "")),
    )
    if not 72 <= settings.dpi <= 600:
        raise ValueError(f"ocr.dpi poza zakresem 72–600: {settings.dpi}")
    return settings


def find_tessdata(language: str, configured: str = "") -> Path | None:
    """Katalog tessdata zawierający `<language>.traineddata` (kolejność: konfiguracja, katalog projektu,
    TESSDATA_PREFIX, katalog wykryty przez PyMuPDF)."""
    candidates: list[Path] = []
    if configured:
        p = Path(configured)
        candidates.append(p if p.is_absolute() else PROJECT_ROOT / p)
    candidates.append(BUNDLED_TESSDATA)
    if env := os.environ.get("TESSDATA_PREFIX"):
        candidates.append(Path(env))
    try:
        detected = pymupdf.get_tessdata()
        if detected:
            candidates.append(Path(detected))
    except Exception:  # brak instalacji Tesseracta — PyMuPDF zgłasza wyjątek
        pass
    for c in candidates:
        if (c / f"{language}.traineddata").is_file():
            return c
    return None


class PyMuPdfTesseract:
    name = "tesseract (PyMuPDF/MuPDF)"

    def __init__(self, language: str = "pol", dpi: int = 300, tessdata_dir: str = ""):
        self.language = language
        self.dpi = dpi
        self.tessdata = find_tessdata(language, tessdata_dir)

    def is_available(self) -> tuple[bool, str]:
        if self.tessdata is None:
            return False, (f"brak pliku {self.language}.traineddata (umieść go w {BUNDLED_TESSDATA} "
                           f"albo ustaw ocr.tessdata_dir / TESSDATA_PREFIX)")
        return True, str(self.tessdata)

    def ocr_page(self, page: pymupdf.Page) -> str:
        textpage = page.get_textpage_ocr(language=self.language, dpi=self.dpi, full=True, tessdata=str(self.tessdata))
        return page.get_text("text", textpage=textpage)


def engine_from_settings(settings: OcrSettings) -> OcrEngine | None:
    if not settings.enabled:
        return None
    return PyMuPdfTesseract(settings.language, settings.dpi, settings.tessdata_dir)
