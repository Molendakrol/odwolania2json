"""Wspólny interfejs klasyfikatorów.

Każdy klasyfikator (regułowy teraz, ML w kolejnych etapach) implementuje ``Classifier``
i zwraca ``ClassificationResult``. Pipeline zależy wyłącznie od tego interfejsu.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Protocol

UNKNOWN = "unknown"


@dataclass(frozen=True)
class DocumentText:
    """Wejście klasyfikatora: tekst dokumentu podzielony na strony + informacja o jakości."""

    pages: list[str]
    has_sufficient_text: bool

    @property
    def text(self) -> str:
        return "\n\n".join(self.pages)


@dataclass
class ClassificationResult:
    label: str                                   # nazwa klasy albo "unknown"
    reason: str                                  # dlaczego taka decyzja (np. "match", "no_text", "veto:...", "conflict")
    scores: dict[str, float] = field(default_factory=dict)          # punktacja per klasa
    signals: dict[str, list[str]] = field(default_factory=dict)     # dopasowane sygnały per klasa
    candidates: list[str] = field(default_factory=list)             # klasy spełniające próg
    confidence: float | None = None               # miejsce na prawdopodobieństwo z modelu ML
    classifier: str = ""

    @property
    def is_unknown(self) -> bool:
        return self.label == UNKNOWN

    def to_dict(self) -> dict:
        return asdict(self)


class Classifier(Protocol):
    name: str

    @property
    def labels(self) -> list[str]:
        """Obsługiwane klasy (bez ``unknown``)."""
        ...

    def classify(self, doc: DocumentText) -> ClassificationResult:
        ...
