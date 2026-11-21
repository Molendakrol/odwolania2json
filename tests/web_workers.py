"""Zastępcze procesy robocze do testów kolejki przetwarzania (P13.3).

Muszą być funkcjami na poziomie modułu (tryb spawn importuje je w nowym procesie). Zachowanie zależy od
nazwy dokumentu zapisanej w meta.json obok `source.pdf`: „hang” — zawieszenie, „crash” — nagłe zakończenie
procesu, „boom” — wyjątek w przetwarzaniu, pozostałe — szybki poprawny wynik.
"""

import json
import os
import time
from pathlib import Path


def fake_worker(conn) -> None:
    while True:
        try:
            job = conn.recv()
        except EOFError:
            return
        if job is None:
            return
        source, out_dir = job
        name = json.loads((Path(source).parent / "meta.json").read_text(encoding="utf-8"))["original_name"]
        if "hang" in name:
            time.sleep(3600)
        if "crash" in name:
            os._exit(1)
        if "boom" in name:
            conn.send(("error", "RuntimeError: awaria testowa"))
            continue
        (Path(out_dir) / "output.json").write_text("{}\n", encoding="utf-8")
        (Path(out_dir) / "diagnostics.json").write_text('{"fake": true}\n', encoding="utf-8")
        conn.send(("ok", {"seconds": 0.01, "summary": {
            "processing_status": "ok", "doc_class": "unknown", "classification_reason": "insufficient_signals",
            "text_source": "native", "page_sources": ["native"], "ocr_status": "not_needed", "ocr_reason": None,
            "ocr_pages_requested": [], "ocr_pages_processed": 0, "ocr_seconds": 0.0, "output_written": True,
            "validation_errors": [], "extraction_error": None, "warnings": 0}}))
