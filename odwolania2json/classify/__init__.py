"""Klasyfikacja dokumentów. Aktualnie: regułowa (RuleClassifier); interfejs w base.py."""

from .base import UNKNOWN, ClassificationResult, Classifier, DocumentText
from .rules import DEFAULT_RULES_PATH, RuleClassifier, RuleConfigError

__all__ = [
    "UNKNOWN",
    "ClassificationResult",
    "Classifier",
    "DocumentText",
    "DEFAULT_RULES_PATH",
    "RuleClassifier",
    "RuleConfigError",
]
