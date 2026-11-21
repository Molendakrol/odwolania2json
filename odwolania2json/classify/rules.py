"""Deterministyczny klasyfikator regułowy sterowany plikiem TOML.

Zasady decyzji (szczegóły w config/classifier_rules.toml):
1. Brak wystarczającego tekstu -> unknown ("no_text").
2. Dopasowane weto -> unknown ("veto:<nazwa>").
3. Klasa jest kandydatem tylko przy łącznym spełnieniu progu punktów, minimalnej liczby
   różnych sygnałów i co najmniej jednego sygnału wymaganego — pojedyncze słowo nie wystarcza.
4. Brak kandydatów -> unknown ("insufficient_signals").
5. Kilku kandydatów z przewagą zwycięzcy < min_margin -> unknown ("conflict").
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from ..normalize import fold_diacritics, matching_text
from .base import UNKNOWN, ClassificationResult, DocumentText

DEFAULT_RULES_PATH = Path(__file__).resolve().parents[2] / "config" / "classifier_rules.toml"
ZONES = ("head", "body")


class RuleConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Signal:
    name: str
    zone: str
    weight: float
    min_count: int
    regexes: tuple[re.Pattern[str], re.Pattern[str]]   # (z polskimi znakami, bez polskich znaków)

    def count(self, zone_texts: dict[str, tuple[str, str]]) -> int:
        plain, folded = zone_texts[self.zone]
        return max(len(self.regexes[0].findall(plain)), len(self.regexes[1].findall(folded)))

    def matches(self, zone_texts: dict[str, tuple[str, str]]) -> bool:
        return self.count(zone_texts) >= self.min_count


@dataclass(frozen=True)
class ClassRule:
    name: str
    min_score: float
    min_signals: int
    required_any: tuple[str, ...]
    signals: tuple[Signal, ...]


@dataclass(frozen=True)
class Veto:
    name: str
    zone: str
    regexes: tuple[tuple[re.Pattern[str], re.Pattern[str]], ...]

    def matches(self, zone_texts: dict[str, tuple[str, str]]) -> bool:
        plain, folded = zone_texts[self.zone]
        return all(r.search(plain) or rf.search(folded) for r, rf in self.regexes)


def _compile(pattern: str, where: str) -> tuple[re.Pattern[str], re.Pattern[str]]:
    try:
        return re.compile(pattern), re.compile(fold_diacritics(pattern))
    except re.error as exc:
        raise RuleConfigError(f"Błędny regex w {where}: {exc}") from exc


def _zone(value: str, where: str) -> str:
    if value not in ZONES:
        raise RuleConfigError(f"Nieznana strefa '{value}' w {where}; dozwolone: {ZONES}")
    return value


class RuleClassifier:
    name = "rules"

    def __init__(self, config: dict):
        try:
            self.head_chars = int(config["zones"]["head_chars"])
            self.body_pages = int(config["zones"]["body_pages"])
            self.min_margin = float(config["decision"]["min_margin"])
            self.rules = tuple(self._parse_class(n, c) for n, c in config["classes"].items())
            self.vetoes = tuple(self._parse_veto(v) for v in config.get("veto", []))
        except KeyError as exc:
            raise RuleConfigError(f"Brak wymaganego klucza konfiguracji: {exc}") from exc
        if not self.rules:
            raise RuleConfigError("Konfiguracja nie definiuje żadnej klasy")

    @classmethod
    def from_file(cls, path: str | Path = DEFAULT_RULES_PATH) -> "RuleClassifier":
        with open(path, "rb") as fh:
            return cls(tomllib.load(fh))

    @property
    def labels(self) -> list[str]:
        return [r.name for r in self.rules]

    @staticmethod
    def _parse_class(name: str, cfg: dict) -> ClassRule:
        if name == UNKNOWN:
            raise RuleConfigError("'unknown' jest zarezerwowane i nie może być klasą w konfiguracji")
        signals = tuple(
            Signal(
                name=s["name"],
                zone=_zone(s.get("zone", "body"), f"{name}.{s['name']}"),
                weight=float(s.get("weight", 1.0)),
                min_count=int(s.get("min_count", 1)),
                regexes=_compile(s["pattern"], f"{name}.{s['name']}"),
            )
            for s in cfg["signals"]
        )
        names = {s.name for s in signals}
        required = tuple(cfg.get("required_any", []))
        missing = [r for r in required if r not in names]
        if missing:
            raise RuleConfigError(f"Klasa {name}: required_any odwołuje się do nieistniejących sygnałów {missing}")
        return ClassRule(name, float(cfg["min_score"]), int(cfg["min_signals"]), required, signals)

    @staticmethod
    def _parse_veto(cfg: dict) -> Veto:
        where = f"veto.{cfg['name']}"
        return Veto(cfg["name"], _zone(cfg.get("zone", "head"), where),
                    tuple(_compile(p, where) for p in cfg["all"]))

    def _zone_texts(self, doc: DocumentText) -> dict[str, tuple[str, str]]:
        body = matching_text("\n\n".join(doc.pages[: self.body_pages]))
        head = body[: self.head_chars]
        return {z: (t, fold_diacritics(t)) for z, t in (("head", head), ("body", body))}

    def classify(self, doc: DocumentText) -> ClassificationResult:
        if not doc.has_sufficient_text:
            return ClassificationResult(UNKNOWN, "no_text", classifier=self.name)

        zone_texts = self._zone_texts(doc)
        scores: dict[str, float] = {}
        matched: dict[str, list[str]] = {}
        candidates: list[str] = []
        for rule in self.rules:
            hits = [s for s in rule.signals if s.matches(zone_texts)]
            names = [s.name for s in hits]
            scores[rule.name] = float(sum(s.weight for s in hits))
            matched[rule.name] = names
            if (scores[rule.name] >= rule.min_score
                    and len(hits) >= rule.min_signals
                    and (not rule.required_any or any(n in rule.required_any for n in names))):
                candidates.append(rule.name)

        result = ClassificationResult(UNKNOWN, "", scores, matched, candidates, classifier=self.name)

        for veto in self.vetoes:
            if veto.matches(zone_texts):
                result.reason = f"veto:{veto.name}"
                return result

        if not candidates:
            result.reason = "insufficient_signals"
            return result

        ranked = sorted(candidates, key=lambda c: scores[c], reverse=True)
        if len(ranked) > 1 and scores[ranked[0]] - scores[ranked[1]] < self.min_margin:
            result.reason = "conflict:" + ",".join(ranked)
            return result

        result.label = ranked[0]
        result.reason = "match"
        return result
