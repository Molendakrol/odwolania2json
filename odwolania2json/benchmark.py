"""Evaluator MVP: manifest -> pipeline (w pamięci) -> metryki klasyfikacji i ekstrakcji -> raport JSON;
porównanie dwóch raportów. METRYKI WEWNĘTRZNE — nie są oficjalnym scoringiem organizatora.

Manifest (JSONL, jedna linia = jeden dokument; ścieżki względem pliku manifestu lub absolutne):
    {"pdf": "dane/przyklad_01/odwolanie.pdf", "label": "odwolanie", "gt": "dane/przyklad_01/odwolanie.json",
     "group": "przyklad_01", "source": "organizer"}
`gt` (ground truth JSON), `group` i `source` są opcjonalne. Dokument bez `gt` jest oceniany tylko w klasyfikacji.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .classify import UNKNOWN
from .evaluation import Comparison, compare
from .pipeline import Pipeline

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LABELS = ["odwolanie", "pelnomocnictwo", "przelew", UNKNOWN]
REPORT_VERSION = 1


class ManifestError(ValueError):
    pass


@dataclass(frozen=True)
class ManifestEntry:
    doc_id: str          # identyfikator = ścieżka PDF tak, jak zapisano w manifeście
    pdf: Path
    label: str
    gt: Path | None
    group: str | None
    source: str | None


def load_manifest(path: str | Path) -> list[ManifestEntry]:
    path = Path(path)
    base = path.parent
    entries: list[ManifestEntry] = []
    seen: set[str] = set()
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ManifestError(f"{path}:{n}: niepoprawny JSON: {exc}") from exc
        for key in ("pdf", "label"):
            if not row.get(key):
                raise ManifestError(f"{path}:{n}: brak pola '{key}'")
        if row["label"] not in LABELS:
            raise ManifestError(f"{path}:{n}: nieznana klasa '{row['label']}' (dozwolone: {LABELS})")
        if row["pdf"] in seen:
            raise ManifestError(f"{path}:{n}: zduplikowany dokument {row['pdf']}")
        seen.add(row["pdf"])
        resolve = lambda p: (Path(p) if Path(p).is_absolute() else base / p)  # noqa: E731
        entries.append(ManifestEntry(
            doc_id=row["pdf"], pdf=resolve(row["pdf"]), label=row["label"],
            gt=resolve(row["gt"]) if row.get("gt") else None,
            group=row.get("group"), source=row.get("source"),
        ))
    if not entries:
        raise ManifestError(f"{path}: manifest jest pusty")
    return entries


# ------------------------------------------------------------------------------- klasyfikacja

def classification_metrics(pairs: list[tuple[str, str]], labels: list[str] = LABELS) -> dict:
    """pairs: (oczekiwana, przewidziana). Macro F1 liczone po klasach obecnych w referencji lub predykcjach."""
    matrix = {g: {p: 0 for p in labels} for g in labels}
    for gold, pred in pairs:
        matrix[gold][pred if pred in labels else UNKNOWN] += 1
    per_class = {}
    for c in labels:
        tp = matrix[c][c]
        fp = sum(matrix[g][c] for g in labels if g != c)
        fn = sum(matrix[c][p] for p in labels if p != c)
        support = tp + fn
        p = tp / (tp + fp) if tp + fp else 0.0
        r = tp / support if support else 0.0
        f1 = 2 * p * r / (p + r) if p + r else 0.0
        per_class[c] = {"precision": round(p, 4), "recall": round(r, 4), "f1": round(f1, 4),
                        "support": support, "predicted": tp + fp}
    active = [c for c in labels if per_class[c]["support"] or per_class[c]["predicted"]]
    correct = sum(matrix[c][c] for c in labels)
    total = len(pairs)
    return {
        "documents": total,
        "accuracy": round(correct / total, 4) if total else 0.0,
        "macro_f1": round(sum(per_class[c]["f1"] for c in active) / len(active), 4) if active else 0.0,
        "per_class": per_class,
        "confusion_matrix": matrix,     # wiersze = oczekiwana, kolumny = przewidziana
        "false_positives_from_unknown": sum(matrix[UNKNOWN][c] for c in labels if c != UNKNOWN),
        "false_unknown": sum(matrix[c][UNKNOWN] for c in labels if c != UNKNOWN),
        "cross_class_errors": sum(matrix[g][p] for g in labels for p in labels
                                  if g != p and UNKNOWN not in (g, p)),
    }


# ------------------------------------------------------------------------------- uruchomienie

def _file_hash(paths: list[Path]) -> str:
    h = hashlib.sha256()
    for p in sorted(paths):
        if p.is_file():
            h.update(p.relative_to(PROJECT_ROOT).as_posix().encode())
            h.update(p.read_bytes())
    return h.hexdigest()[:16]


def config_fingerprint() -> str:
    """Skrót kodu i konfiguracji — pozwala odróżnić wersje pipeline'u w porównaniach raportów."""
    files = list((PROJECT_ROOT / "config").rglob("*.toml")) + list((PROJECT_ROOT / "config" / "templates").glob("*.json"))
    files += list((PROJECT_ROOT / "odwolania2json").rglob("*.py"))
    return _file_hash(files)


def _prediction(result) -> Any:
    """To, co trafiłoby do output/: dane szablonu albo {} dla unknown."""
    return result.fields.data if result.fields is not None else {}


def run_evaluation(entries: list[ManifestEntry], pipeline: Pipeline, label: str = "") -> dict:
    docs = []
    pairs: list[tuple[str, str]] = []
    extraction = Comparison()
    per_type: dict[str, Comparison] = {}
    for e in entries:
        res = pipeline.process_file(e.pdf)
        pairs.append((e.label, res.doc_class))
        doc = {
            "doc_id": e.doc_id, "group": e.group, "source": e.source,
            "expected_class": e.label, "predicted_class": res.doc_class,
            "class_correct": e.label == res.doc_class, "reason": res.classification.reason,
            "text_source": res.extraction.text_source.value, "extraction_status": res.extraction.status.value,
            "fields": None, "mismatches": [],
        }
        if e.gt is not None:
            gold = json.loads(e.gt.read_text(encoding="utf-8"))
            single = compare(_prediction(res), gold, e.doc_id)
            compare(_prediction(res), gold, e.doc_id, extraction)
            compare(_prediction(res), gold, e.doc_id, per_type.setdefault(e.label, Comparison()))
            doc["fields"] = single.total.to_dict()
            doc["mismatches"] = [{"path": p, "status": s, "pred": pv, "gold": gv} for _, p, s, pv, gv in single.details]
        docs.append(doc)

    with_gt = [d for d in docs if d["fields"] is not None]
    worst_fields = sorted(((k, c) for k, c in extraction.by_field.items() if c.scored + c.extra > c.correct),
                          key=lambda kc: (-(kc[1].wrong + kc[1].missing + kc[1].extra), kc[0]))
    worst_docs = sorted((d for d in with_gt if d["mismatches"]), key=lambda d: (-len(d["mismatches"]), d["doc_id"]))
    return {
        "report_version": REPORT_VERSION,
        "label": label,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "config_fingerprint": config_fingerprint(),
        "ocr_enabled": pipeline.ocr_engine is not None,
        "note": "METRYKI WEWNĘTRZNE — nie są oficjalnym scoringiem organizatora",
        "classification": classification_metrics(pairs),
        "extraction": {
            "documents_with_ground_truth": len(with_gt),
            "documents_fully_correct": sum(1 for d in with_gt if not d["mismatches"]),
            "overall": extraction.total.to_dict(),
            "per_type": {t: c.total.to_dict() for t, c in sorted(per_type.items())},
            "per_field": {k: c.to_dict() for k, c in sorted(extraction.by_field.items())},
            "worst_fields": [{"field": k, **c.to_dict()} for k, c in worst_fields[:15]],
            "worst_documents": [{"doc_id": d["doc_id"], "errors": len(d["mismatches"])} for d in worst_docs[:15]],
        },
        "documents": docs,
    }


def save_report(report: dict, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def load_report(path: str | Path) -> dict:
    report = json.loads(Path(path).read_text(encoding="utf-8"))
    if report.get("report_version") != REPORT_VERSION:
        raise ValueError(f"{path}: nieobsługiwana wersja raportu {report.get('report_version')!r}")
    return report


# ------------------------------------------------------------------------------- formatowanie

def format_summary(report: dict) -> str:
    c, x = report["classification"], report["extraction"]
    lines = [report["note"], f"Run: {report['label'] or '-'} | {report['created_at']} | config {report['config_fingerprint']}"
             f" | OCR {'tak' if report['ocr_enabled'] else 'nie'}", "",
             "KLASYFIKACJA",
             f"  dokumentów: {c['documents']} | accuracy: {c['accuracy']:.4f} | macro F1: {c['macro_f1']:.4f}",
             f"  negatyw -> klasa pozytywna (FP): {c['false_positives_from_unknown']} | pozytyw -> unknown: "
             f"{c['false_unknown']} | pomyłki między klasami: {c['cross_class_errors']}",
             f"  {'klasa':16s} {'P':>6} {'R':>6} {'F1':>6} {'wsparcie':>9}"]
    for cls, m in c["per_class"].items():
        lines.append(f"  {cls:16s} {m['precision']:6.3f} {m['recall']:6.3f} {m['f1']:6.3f} {m['support']:9d}")
    labels = list(c["confusion_matrix"])
    lines.append("  macierz pomyłek (wiersz = oczekiwana, kolumna = przewidziana):")
    lines.append("  " + " " * 16 + "".join(f"{l[:12]:>13}" for l in labels))
    for g in labels:
        lines.append(f"  {g:16s}" + "".join(f"{c['confusion_matrix'][g][p]:13d}" for p in labels))
    o = x["overall"]
    lines += ["", "EKSTRAKCJA",
              f"  dokumentów z ground truth: {x['documents_with_ground_truth']} | w pełni poprawnych: {x['documents_fully_correct']}",
              f"  pola: poprawne {o['correct']}, błędne {o['wrong']}, brakujące {o['missing']}, nadmiarowe {o['extra']}",
              f"  accuracy pól: {o['accuracy']:.4f} | P {o['precision']:.4f} R {o['recall']:.4f} F1 {o['f1']:.4f}"
              " (P/R/F1 na wartościach nie-null)",
              f"  {'typ':16s} {'acc':>6} {'F1':>6} {'ok':>5} {'err':>5} {'miss':>5} {'extra':>5}"]
    for t, m in x["per_type"].items():
        lines.append(f"  {t:16s} {m['accuracy']:6.3f} {m['f1']:6.3f} {m['correct']:5d} {m['wrong']:5d} {m['missing']:5d} {m['extra']:5d}")
    if x["worst_fields"]:
        lines.append("  najczęściej błędne pola:")
        for f in x["worst_fields"][:10]:
            lines.append(f"    {f['field']:55s} err {f['wrong']:3d} miss {f['missing']:3d} extra {f['extra']:3d} acc {f['accuracy']:.3f}")
    if x["worst_documents"]:
        lines.append("  dokumenty z największą liczbą błędów:")
        for d in x["worst_documents"][:10]:
            lines.append(f"    {d['errors']:3d}  {d['doc_id']}")
    wrong_cls = [d for d in report["documents"] if not d["class_correct"]]
    if wrong_cls:
        lines.append("\nBŁĘDNA KLASYFIKACJA:")
        for d in wrong_cls[:20]:
            lines.append(f"  {d['doc_id']}: oczekiwano {d['expected_class']}, jest {d['predicted_class']} ({d['reason']})")
    return "\n".join(lines)


# ------------------------------------------------------------------------------- porównanie runów

def compare_reports(base: dict, new: dict) -> dict:
    """Porównuje dwa raporty na wspólnych dokumentach (po doc_id)."""
    b_docs = {d["doc_id"]: d for d in base["documents"]}
    n_docs = {d["doc_id"]: d for d in new["documents"]}
    common = sorted(set(b_docs) & set(n_docs))

    def delta(a: float, b: float) -> float:
        return round(b - a, 4)

    cls_fixed = [i for i in common if not b_docs[i]["class_correct"] and n_docs[i]["class_correct"]]
    cls_broken = [i for i in common if b_docs[i]["class_correct"] and not n_docs[i]["class_correct"]]

    field_fixed, field_broken = [], []
    for i in common:
        b_mis = {m["path"]: m for m in b_docs[i]["mismatches"]}
        n_mis = {m["path"]: m for m in n_docs[i]["mismatches"]}
        if b_docs[i]["fields"] is None or n_docs[i]["fields"] is None:
            continue
        field_fixed += [{"doc_id": i, "path": p, "was": b_mis[p]} for p in sorted(set(b_mis) - set(n_mis))]
        field_broken += [{"doc_id": i, "path": p, "now": n_mis[p]} for p in sorted(set(n_mis) - set(b_mis))]

    per_field = {}
    for f in sorted(set(base["extraction"]["per_field"]) | set(new["extraction"]["per_field"])):
        a = base["extraction"]["per_field"].get(f, {}).get("accuracy", 0.0)
        b = new["extraction"]["per_field"].get(f, {}).get("accuracy", 0.0)
        if a != b:
            per_field[f] = {"before": a, "after": b, "delta": delta(a, b)}

    bc, nc = base["classification"], new["classification"]
    bx, nx = base["extraction"]["overall"], new["extraction"]["overall"]
    return {
        "base": {"label": base.get("label"), "created_at": base.get("created_at"),
                 "config": base.get("config_fingerprint"), "ocr": base.get("ocr_enabled")},
        "new": {"label": new.get("label"), "created_at": new.get("created_at"),
                "config": new.get("config_fingerprint"), "ocr": new.get("ocr_enabled")},
        "common_documents": len(common),
        "only_in_base": sorted(set(b_docs) - set(n_docs)),
        "only_in_new": sorted(set(n_docs) - set(b_docs)),
        "classification": {k: {"before": bc[k], "after": nc[k], "delta": delta(bc[k], nc[k])}
                           for k in ("accuracy", "macro_f1")},
        "extraction": {k: {"before": bx[k], "after": nx[k], "delta": delta(bx[k], nx[k])}
                       for k in ("accuracy", "f1", "correct", "wrong", "missing", "extra")},
        "classification_fixed": cls_fixed,
        "classification_regressions": cls_broken,
        "fields_fixed": field_fixed,
        "fields_regressions": field_broken,
        "per_field_accuracy_changes": per_field,
        "has_regressions": bool(cls_broken or field_broken),
    }


def format_comparison(cmp: dict, limit: int = 20) -> str:
    def sign(v: float) -> str:
        return f"{v:+.4f}" if isinstance(v, float) else f"{v:+d}"

    def run(r: dict, default: str) -> str:
        return f"{r['label'] or default} (config {r['config']}, OCR {'tak' if r.get('ocr') else 'nie'})"

    lines = [f"PORÓWNANIE: {run(cmp['base'], 'bazowy')} -> {run(cmp['new'], 'nowy')}",
             f"wspólne dokumenty: {cmp['common_documents']} | tylko w bazowym: {len(cmp['only_in_base'])} | "
             f"tylko w nowym: {len(cmp['only_in_new'])}"]
    for section, title in (("classification", "KLASYFIKACJA"), ("extraction", "EKSTRAKCJA")):
        lines.append(title + ":")
        for k, v in cmp[section].items():
            lines.append(f"  {k:10s} {v['before']} -> {v['after']} ({sign(v['delta'])})")
    lines.append(f"Klasyfikacja naprawiona: {len(cmp['classification_fixed'])} | regresje: {len(cmp['classification_regressions'])}")
    for i in cmp["classification_regressions"][:limit]:
        lines.append(f"  REGRESJA klasy: {i}")
    lines.append(f"Pola naprawione: {len(cmp['fields_fixed'])} | regresje pól: {len(cmp['fields_regressions'])}")
    for r in cmp["fields_regressions"][:limit]:
        lines.append(f"  REGRESJA: {r['doc_id']} :: {r['path']} [{r['now']['status']}] "
                     f"pred={r['now']['pred']!r} gold={r['now']['gold']!r}")
    for r in cmp["fields_fixed"][:limit]:
        lines.append(f"  naprawione: {r['doc_id']} :: {r['path']}")
    if cmp["per_field_accuracy_changes"]:
        lines.append("Zmiany accuracy per pole:")
        for f, v in cmp["per_field_accuracy_changes"].items():
            lines.append(f"  {f:55s} {v['before']:.3f} -> {v['after']:.3f} ({v['delta']:+.4f})")
    lines.append("WYNIK: " + ("SĄ REGRESJE" if cmp["has_regressions"] else "brak regresji"))
    return "\n".join(lines)


# ------------------------------------------------------------------------------- manifest organizatora

ORGANIZER_FILES = {"odwolanie.pdf": ("odwolanie", "odwolanie.json"),
                   "pelnomocnictwo.pdf": ("pelnomocnictwo", "pelnomocnictwo.json"),
                   "potwierdzenie_przelewu.pdf": ("przelew", "przelew.json"),
                   "przelew.pdf": ("przelew", "przelew.json")}   # nazwa używana przez organizatora od 21.11.2026


def build_organizer_manifest(dane_dir: str | Path, out_path: str | Path) -> int:
    """Manifest dla katalogu `dane` z repozytorium organizatora (układ przyklad_XX/ + negatywne/)."""
    dane = Path(dane_dir).resolve()
    out_path = Path(out_path)
    rows = []
    for pdf in sorted(dane.rglob("*.pdf")):
        rel = pdf.relative_to(dane)
        group = rel.parts[0]
        if group == "skany":   # skany organizatora mają osobne manifesty (DEV / HOLDOUT)
            continue
        label, gt_name = ORGANIZER_FILES.get(pdf.name, (UNKNOWN, None))
        if group == "negatywne":
            label, gt_name = UNKNOWN, None
        gt = pdf.parent / gt_name if gt_name and (pdf.parent / gt_name).exists() else None
        rows.append({"pdf": str(pdf), "label": label, "gt": str(gt) if gt else None,
                     "group": group, "source": "organizer"})
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return len(rows)


def label_counts(entries: list[ManifestEntry]) -> dict[str, int]:
    return dict(sorted(Counter(e.label for e in entries).items()))
