"""Etap 2: ekstrakcja pól do JSON wg szablonu klasy.

Wejście: tekst dokumentu (strony) + klasa z klasyfikatora. Wyjście: FieldResult z danymi
w dokładnym kształcie szablonu (brak informacji = null) i wynikiem walidacji typów.
Klasa `unknown` nie trafia do ekstrakcji.
"""

from __future__ import annotations

import tomllib
from collections.abc import Callable
from pathlib import Path

from .base import ExtractionContext, FieldResult
from .odwolanie import extract_odwolanie
from .pelnomocnictwo import extract_pelnomocnictwo
from .przelew import extract_przelew
from .template import TEMPLATES_DIR, build_output, load_template, validate

EXTRACTION_CONFIG = Path(__file__).resolve().parents[2] / "config" / "extraction.toml"

Extractor = Callable[[ExtractionContext, dict], FieldResult]

# Klasa dokumentu -> ekstraktor. Nazwa klasy = nazwa szablonu w config/templates/.
EXTRACTORS: dict[str, Extractor] = {
    "odwolanie": extract_odwolanie,
    "pelnomocnictwo": extract_pelnomocnictwo,
    "przelew": extract_przelew,
}


class FieldExtractor:
    def __init__(self, config_path: Path = EXTRACTION_CONFIG, templates_dir: Path = TEMPLATES_DIR):
        with open(config_path, "rb") as fh:
            self.config = tomllib.load(fh)
        self.templates = {label: load_template(label, templates_dir) for label in EXTRACTORS}

    def supports(self, label: str) -> bool:
        return label in EXTRACTORS

    def extract(self, label: str, pages: list[str]) -> FieldResult:
        if not self.supports(label):
            raise ValueError(f"Brak ekstraktora dla klasy '{label}'")
        template = self.templates[label]
        try:
            raw = EXTRACTORS[label](ExtractionContext(pages), self.config)
        except Exception as exc:  # błąd ekstraktora nie może zatrzymać przetwarzania sprawy
            raw = FieldResult(data={}, warnings=[f"błąd ekstraktora: {type(exc).__name__}: {exc}"])
        raw.data = build_output(template, raw.data)
        raw.validation_errors = validate(raw.data, template)
        return raw


__all__ = ["FieldExtractor", "FieldResult", "EXTRACTORS", "build_output", "validate", "load_template"]
