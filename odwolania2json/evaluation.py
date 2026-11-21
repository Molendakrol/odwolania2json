"""Wewnętrzna ocena ekstrakcji pól (METRYKA WEWNĘTRZNA — sposób liczenia F1 przez organizatora
nie jest potwierdzony).

Porównanie rekursywne predykcji z referencją na poziomie liści JSON (ścieżka z indeksem tablicy,
np. `Zamawiajacy[1].nip`). Każdy liść dostaje jeden status:
- correct — klucz jest w obu, wartości równe (także null == null),
- wrong   — klucz jest w obu, wartości różne (także null vs wartość, inny typ, inna struktura),
- missing — klucz/element jest w referencji, a w predykcji go brak (brak klucza ≠ null),
- extra   — klucz/element jest w predykcji, a w referencji go brak.

Metryki:
- accuracy pól = correct / (correct + wrong + missing)   (liście referencji; extra raportowane osobno),
- P/R/F1 liczone na wartościach nie-null:
  TP: correct i referencja nie-null; FP: predykcja nie-null i nie-correct (wrong/extra);
  FN: referencja nie-null i nie-correct (wrong/missing).
Normalizacja (wyłącznie bezpieczna): stringi — zwinięcie białych znaków; liczby — porównanie liczbowe
(int 200 == float 200.0). String "200" ≠ liczba 200, bool ≠ liczba.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

ABSENT = object()   # brak predykcji (np. pusty plik) — wszystkie liście referencji są „missing”

STATUSES = ("correct", "wrong", "missing", "extra")


def flatten(obj: Any, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(flatten(v, f"{prefix}.{k}" if prefix else k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.update(flatten(v, f"{prefix}[{i}]"))
    else:
        out[prefix] = obj
    return out


def generic_path(path: str) -> str:
    return re.sub(r"\[\d+\]", "[]", path)


def _norm(v: Any) -> Any:
    if isinstance(v, str):
        return ("str", re.sub(r"\s+", " ", v).strip())
    if isinstance(v, bool):
        return ("bool", v)
    if isinstance(v, (int, float)):
        return ("num", float(v))
    return ("other", v)


def values_equal(pred: Any, gold: Any) -> bool:
    return _norm(pred) == _norm(gold)


def _join(prefix: str, key: str) -> str:
    return f"{prefix}.{key}" if prefix else key


def diff(pred: Any, gold: Any, path: str = "") -> Iterator[tuple[str, str, Any, Any]]:
    """Rekursywne porównanie: (ścieżka, status, wartość predykcji, wartość referencji) dla każdego liścia."""
    if isinstance(gold, dict):
        if isinstance(pred, dict):
            for k, gv in gold.items():
                if k in pred:
                    yield from diff(pred[k], gv, _join(path, k))
                else:
                    for p, v in flatten(gv, _join(path, k)).items():
                        yield p, "missing", None, v
            for k, pv in pred.items():
                if k not in gold:
                    for p, v in flatten(pv, _join(path, k)).items():
                        yield p, "extra", v, None
            return
        status = "missing" if pred is ABSENT else "wrong"
        for p, v in flatten(gold, path).items():
            yield p, status, (None if pred is ABSENT else pred), v
        return
    if isinstance(gold, list):
        if isinstance(pred, list):
            for i in range(max(len(gold), len(pred))):
                p_i = f"{path}[{i}]"
                if i < len(gold) and i < len(pred):
                    yield from diff(pred[i], gold[i], p_i)
                elif i < len(gold):
                    for p, v in flatten(gold[i], p_i).items():
                        yield p, "missing", None, v
                else:
                    for p, v in flatten(pred[i], p_i).items():
                        yield p, "extra", v, None
            return
        status = "missing" if pred is ABSENT else "wrong"
        for p, v in flatten(gold, path).items():
            yield p, status, (None if pred is ABSENT else pred), v
        return
    if pred is ABSENT:
        yield path, "missing", None, gold
    elif isinstance(pred, (dict, list)):
        yield path, "wrong", pred, gold
    else:
        yield path, "correct" if values_equal(pred, gold) else "wrong", pred, gold


@dataclass
class Counts:
    correct: int = 0
    wrong: int = 0
    missing: int = 0
    extra: int = 0
    tp: int = 0
    fp: int = 0
    fn: int = 0

    def record(self, status: str, pred: Any, gold: Any) -> None:
        setattr(self, status, getattr(self, status) + 1)
        if status == "correct":
            if gold is not None:
                self.tp += 1
            return
        if pred is not None and status in ("wrong", "extra"):
            self.fp += 1
        if gold is not None and status in ("wrong", "missing"):
            self.fn += 1

    def add(self, other: "Counts") -> None:
        for name in ("correct", "wrong", "missing", "extra", "tp", "fp", "fn"):
            setattr(self, name, getattr(self, name) + getattr(other, name))

    @property
    def scored(self) -> int:
        return self.correct + self.wrong + self.missing

    @property
    def accuracy(self) -> float:
        return self.correct / self.scored if self.scored else 0.0

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0

    def to_dict(self) -> dict:
        return {
            "correct": self.correct, "wrong": self.wrong, "missing": self.missing, "extra": self.extra,
            "accuracy": round(self.accuracy, 4), "tp": self.tp, "fp": self.fp, "fn": self.fn,
            "precision": round(self.precision, 4), "recall": round(self.recall, 4), "f1": round(self.f1, 4),
        }


@dataclass
class Comparison:
    total: Counts = field(default_factory=Counts)
    by_field: dict[str, Counts] = field(default_factory=lambda: defaultdict(Counts))
    by_document: dict[str, Counts] = field(default_factory=lambda: defaultdict(Counts))
    mismatches: list[tuple[str, str, Any, Any]] = field(default_factory=list)   # (dokument, pole, pred, gold)
    details: list[tuple[str, str, str, Any, Any]] = field(default_factory=list)   # (dokument, pole, status, pred, gold)


def compare(pred: Any, gold: Any, doc_id: str = "", result: Comparison | None = None) -> Comparison:
    """Porównuje predykcję z referencją. `pred=None` (brak predykcji) jest traktowane jak ABSENT."""
    result = result or Comparison()
    pred = ABSENT if pred is None and gold is not None else pred
    result.by_document.setdefault(doc_id, Counts())
    for path, status, pv, gv in diff(pred, gold):
        result.total.record(status, pv, gv)
        result.by_field[generic_path(path)].record(status, pv, gv)
        result.by_document[doc_id].record(status, pv, gv)
        if status != "correct":
            result.mismatches.append((doc_id, path, pv, gv))
            result.details.append((doc_id, path, status, pv, gv))
    return result


# Dopasowanie plików referencyjnych z danych organizatora do nazw plików wejściowych.
DEFAULT_GOLD_ALIASES = {"przelew.json": "potwierdzenie_przelewu.json"}


def load_prediction(path: Path) -> Any:
    """Plik predykcji: brak lub 0 bajtów = ABSENT; w przeciwnym razie wczytany JSON."""
    if not path.exists() or path.stat().st_size == 0:
        return ABSENT
    return json.loads(path.read_text(encoding="utf-8"))


def evaluate_dirs(pred_dir: Path, gold_dir: Path, aliases: dict[str, str] | None = None) -> Comparison:
    """Porównuje każdy plik `gold_dir/**/X.json` z `pred_dir/**/X.json` (z aliasami nazw)."""
    aliases = DEFAULT_GOLD_ALIASES if aliases is None else aliases
    result = Comparison()
    for gold_path in sorted(Path(gold_dir).rglob("*.json")):
        rel = gold_path.relative_to(gold_dir)
        pred_path = Path(pred_dir) / rel.parent / aliases.get(rel.name, rel.name)
        if not pred_path.exists():   # np. organizator zmienił nazwę PDF tak, że pokrywa się z nazwą referencji
            pred_path = Path(pred_dir) / rel
        gold = json.loads(gold_path.read_text(encoding="utf-8"))
        compare(load_prediction(pred_path), gold, rel.as_posix(), result)
    return result


def format_report(cmp: Comparison, max_mismatches: int = 30) -> str:
    lines = ["METRYKI WEWNĘTRZNE (nie są oficjalnym scoringiem organizatora)",
             f"{'pole':55s} {'OK':>4} {'ERR':>4} {'MISS':>4} {'EXTR':>4} {'ACC':>6} {'F1':>6}"]
    for name, c in sorted(cmp.by_field.items()):
        lines.append(f"{name:55s} {c.correct:4d} {c.wrong:4d} {c.missing:4d} {c.extra:4d} {c.accuracy:6.3f} {c.f1:6.3f}")
    t = cmp.total
    lines.append(f"{'RAZEM':55s} {t.correct:4d} {t.wrong:4d} {t.missing:4d} {t.extra:4d} {t.accuracy:6.3f} {t.f1:6.3f}")
    lines.append(f"(P={t.precision:.3f} R={t.recall:.3f} na wartościach nie-null; TP={t.tp} FP={t.fp} FN={t.fn})")
    if cmp.details:
        lines.append(f"\nPierwsze rozbieżności ({min(max_mismatches, len(cmp.details))} z {len(cmp.details)}):")
        for doc, path, status, pv, gv in cmp.details[:max_mismatches]:
            lines.append(f"  [{status}] {doc} :: {path}\n      pred={pv!r}\n      gold={gv!r}")
    return "\n".join(lines)
