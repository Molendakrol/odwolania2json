"""Wspólne typy etapu ekstrakcji pól."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ExtractionContext:
    """Tekst dokumentu w dwóch postaciach: z liniami (układy etykieta/wartość) i spłaszczony (akapity).

    Przy tworzeniu stosowane są wyłącznie kontekstowe korekty typowych błędów OCR
    (forma prawna 'Sp. z 0.0.', przyimek 'Z'/'W' wielką literą w środku zdania) — bez globalnych zamian znaków.
    """

    pages: list[str]

    def __post_init__(self) -> None:
        from .normalizers import fix_legal_form, fix_lone_prepositions
        self.pages = [fix_lone_prepositions(fix_legal_form(p)) for p in self.pages]

    @property
    def text(self) -> str:
        return "\n".join(self.pages)

    @property
    def lines(self) -> list[str]:
        return [ln.strip() for ln in self.text.split("\n") if ln.strip()]

    @property
    def flat(self) -> str:
        return re.sub(r"\s+", " ", self.text).strip()


@dataclass
class FieldResult:
    """Wynik ekstrakcji: dane w kształcie szablonu + uzasadnienia (skąd wartość) + ostrzeżenia."""

    data: dict[str, Any]
    evidence: dict[str, str] = field(default_factory=dict)   # ścieżka pola -> fragment źródłowy
    warnings: list[str] = field(default_factory=list)
    validation_errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "data": self.data,
            "evidence": self.evidence,
            "warnings": self.warnings,
            "validation_errors": self.validation_errors,
        }
