"""Zapis wyników diagnostycznych na dysk (oddzielony od pipeline'u i CLI)."""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

from .pipeline import DocumentResult

SUMMARY_FIELDS = ["relative_path", "status", "doc_class", "reason", "text_source", "ocr_status", "ocr_reason",
                  "ocr_pages", "ocr_seconds", "pages", "meaningful_chars", "scores", "error"]


def _summary_row(r: DocumentResult) -> dict:
    return {
        "relative_path": r.relative_path,
        "status": r.status.value,
        "doc_class": r.doc_class,
        "reason": r.classification.reason,
        "text_source": r.extraction.text_source.value,
        "ocr_status": r.extraction.ocr.status,
        "ocr_reason": r.extraction.ocr.reason or "",
        "ocr_pages": r.extraction.ocr.pages_processed,
        "ocr_seconds": r.extraction.ocr.seconds,
        "pages": r.extraction.page_count,
        "meaningful_chars": r.extraction.meaningful_chars,
        "scores": ";".join(f"{k}={v:g}" for k, v in r.classification.scores.items()),
        "error": r.extraction.error or "; ".join(r.extraction.ocr.errors),
    }


def write_diagnostics(results: list[DocumentResult], out_dir: str | Path, save_text: bool = False) -> Path:
    """Zapisuje: <out>/<ścieżka>.diag.json per dokument, opcjonalnie .txt, oraz summary.json i summary.csv."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for r in results:
        target = out_dir / Path(r.relative_path).with_suffix(".diag.json")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(r.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if save_text:
            target.with_name(Path(r.relative_path).stem + ".txt").write_text(r.text, encoding="utf-8")

    rows = [_summary_row(r) for r in results]
    summary = {
        "documents": len(results),
        "by_class": dict(sorted(Counter(r.doc_class for r in results).items())),
        "by_status": dict(sorted(Counter(r.status.value for r in results).items())),
        "by_text_source": dict(sorted(Counter(r.extraction.text_source.value for r in results).items())),
        "results": rows,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with open(out_dir / "summary.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=SUMMARY_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return out_dir
