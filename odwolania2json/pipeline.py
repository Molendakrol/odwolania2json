"""Pipeline: PDF -> ekstrakcja tekstu (native, OCR jako fallback) -> normalizacja -> kontrola jakości -> klasyfikacja -> ekstrakcja pól -> wynik.

Moduł nie zna formatu zapisu ani CLI — zwraca ``DocumentResult``. Klasyfikacja i ekstrakcja pól są
rozdzielone: ekstraktor dostaje gotową klasę i nie zmienia jej; `unknown` nie trafia do ekstrakcji.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .classify import UNKNOWN, ClassificationResult, Classifier, DocumentText, RuleClassifier
from .extract import FieldExtractor, FieldResult
from .ocr import OcrEngine, engine_from_settings, load_ocr_settings
from .pdf_text import ExtractionResult, ExtractionStatus, extract_text

_DEFAULT = object()   # znacznik: silnik OCR z konfiguracji (config/app.toml)


class ProcessingStatus(str, Enum):
    OK = "ok"                    # tekst wyciągnięty i dokument sklasyfikowany (także jako unknown)
    NO_TEXT = "no_text"          # brak/za mało tekstu (np. skan bez OCR) -> unknown
    ERROR = "error"              # pliku nie dało się odczytać -> unknown


@dataclass
class DocumentResult:
    file_name: str
    relative_path: str           # ścieżka względem katalogu wejściowego (np. case1/odwolanie.pdf)
    status: ProcessingStatus
    doc_class: str
    extraction: ExtractionResult
    classification: ClassificationResult
    fields: FieldResult | None = None   # None dla unknown albo gdy ekstrakcja pól jest wyłączona

    @property
    def text(self) -> str:
        return self.extraction.text

    def to_dict(self, include_text: bool = False) -> dict:
        data = {
            "file_name": self.file_name,
            "relative_path": self.relative_path,
            "status": self.status.value,
            "doc_class": self.doc_class,
            "extraction": self.extraction.diagnostics(),
            "classification": self.classification.to_dict(),
            "fields": self.fields.to_dict() if self.fields else None,
        }
        if include_text:
            data["text"] = self.text
        return data


class Pipeline:
    def __init__(self, classifier: Classifier | None = None, field_extractor: FieldExtractor | None = None,
                 extract_fields: bool = True, ocr_engine: "OcrEngine | None | object" = _DEFAULT):
        """ocr_engine: domyślnie z config/app.toml; None = OCR wyłączony; albo własny silnik (np. w testach)."""
        self.classifier = classifier or RuleClassifier.from_file()
        self.field_extractor = (field_extractor or FieldExtractor()) if extract_fields else None
        self.ocr_engine = engine_from_settings(load_ocr_settings()) if ocr_engine is _DEFAULT else ocr_engine

    def process_file(self, path: str | Path, base_dir: str | Path | None = None) -> DocumentResult:
        path = Path(path)
        rel = path.relative_to(base_dir).as_posix() if base_dir else path.name
        extraction = extract_text(path, self.ocr_engine)
        if extraction.status is ExtractionStatus.ERROR:
            status = ProcessingStatus.ERROR
            classification = ClassificationResult(UNKNOWN, "extraction_error", classifier=self.classifier.name)
        else:
            status = ProcessingStatus.OK if extraction.has_sufficient_text else ProcessingStatus.NO_TEXT
            classification = self.classifier.classify(
                DocumentText(pages=extraction.pages, has_sufficient_text=extraction.has_sufficient_text)
            )
        fields = None
        if self.field_extractor and self.field_extractor.supports(classification.label):
            fields = self.field_extractor.extract(classification.label, extraction.pages)
        return DocumentResult(path.name, rel, status, classification.label, extraction, classification, fields)

    def process_dir(self, input_dir: str | Path) -> list[DocumentResult]:
        input_dir = Path(input_dir)
        return [self.process_file(p, input_dir) for p in find_pdfs(input_dir)]


def find_pdfs(input_dir: str | Path) -> list[Path]:
    """Wszystkie pliki .pdf (bez względu na wielkość liter rozszerzenia), rekurencyjnie, w stałej kolejności."""
    input_dir = Path(input_dir)
    return sorted(p for p in input_dir.rglob("*") if p.is_file() and p.suffix.lower() == ".pdf")
