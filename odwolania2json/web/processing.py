"""Przetwarzanie dokumentów dodanych w interfejsie (P13.3).

Architektura (wyłącznie biblioteka standardowa):
- `ProcessingQueue` — kolejka w pamięci + jeden wątek-dyspozytor w procesie serwera. Serwer HTTP tylko
  dopisuje dokumenty do kolejki i od razu odpowiada; stan dokumentów jest w `meta.json` magazynu.
- `ProcessRunner` — jeden długo żyjący proces roboczy (multiprocessing, tryb spawn), który przetwarza
  dokumenty po kolei. MuPDF nie jest bezpieczny wątkowo, a OCR nietypowego pliku może trwać bardzo długo
  albo zakończyć proces — dlatego pipeline działa poza procesem serwera, z limitem czasu na dokument.
  Po przekroczeniu limitu albo awarii proces jest kończony i uruchamiany ponownie; błąd dotyczy tylko
  jednego dokumentu.
- `process_document` — jedyne miejsce wywołujące pipeline. Korzysta z istniejących funkcji bez zmian:
  `Pipeline.process_file`, `write_outputs` (ta sama walidacja i ten sam format JSON co CLI) oraz
  `DocumentResult.to_dict` (diagnostyka bez pełnego tekstu dokumentu).
"""

from __future__ import annotations

import collections
import json
import multiprocessing
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .. import __version__
from .documents import (INTERRUPTED_NOTE, STATUS_ADDED, STATUS_FAILED, STATUS_PROCESSED, STATUS_PROCESSING,
                        STATUS_QUEUED, DocumentStore)

OUTPUT_FILE = "output.json"
DIAGNOSTICS_FILE = "diagnostics.json"
TIMEOUT_BASE = 60.0          # s na dokument ...
TIMEOUT_PER_PAGE = 45.0      # ... + s na stronę (OCR skanu: ok. 8–10 s/stronę na typowym laptopie)

# Komunikaty dla użytkownika (status `failed`).
MSG_UNREADABLE = "Nie udało się odczytać pliku PDF."
MSG_REJECTED = "Wynik nie przeszedł walidacji ze wzorcem JSON i nie został zapisany."
MSG_TIMEOUT = "Przekroczono limit czasu przetwarzania ({limit} s). Dokument może być zbyt duży lub nieczytelny."
MSG_CRASH = "Proces przetwarzania zakończył się nieoczekiwanie."
MSG_ERROR = "Wystąpił nieoczekiwany błąd przetwarzania."


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def timeout_for(pages: int | None) -> float:
    return TIMEOUT_BASE + TIMEOUT_PER_PAGE * max(pages or 1, 1)


# ---------------------------------------------------------------------------------------------- worker
def process_document(pipeline, source: str | Path, out_dir: str | Path, unknown_mode: str) -> dict:
    """Przetwarza jeden PDF istniejącym pipeline'em i zapisuje wyniki do `out_dir`. Zwraca skrót wyniku."""
    from ..output import output_path, write_outputs

    started = time.perf_counter()
    out_dir = Path(out_dir)
    result = pipeline.process_file(source)
    report = write_outputs([result], out_dir, unknown_mode)          # walidacja + zapis jak w CLI
    written = bool(report.written)
    if written:
        output_path(out_dir, result).rename(out_dir / OUTPUT_FILE)
    rejected = [err for _, errors in report.rejected for err in errors]
    seconds = round(time.perf_counter() - started, 3)

    ext, ocr = result.extraction, result.extraction.ocr
    summary = {
        "processing_status": result.status.value,           # ok / no_text / error (ProcessingStatus)
        "doc_class": result.doc_class,
        "classification_reason": result.classification.reason,
        "text_source": ext.text_source.value,               # native / ocr / mixed / none
        "page_sources": list(ext.page_sources),
        "ocr_status": ocr.status,                           # not_needed / disabled / unavailable / ok / partial / failed
        "ocr_reason": ocr.reason,
        "ocr_pages_requested": list(ocr.pages_requested),
        "ocr_pages_processed": ocr.pages_processed,
        "ocr_seconds": ocr.seconds,
        "output_written": written,
        "validation_errors": rejected,
        "extraction_error": ext.error,
        "warnings": len(result.fields.warnings) if result.fields else 0,
    }
    diagnostics = {
        "app_version": __version__,
        "processed_at": _now(),
        "seconds": seconds,
        "output_file": OUTPUT_FILE if written else None,
        "rejected": rejected,
        "result": result.to_dict(),
    }
    (out_dir / DIAGNOSTICS_FILE).write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"summary": summary, "seconds": seconds}


def worker_main(conn, ocr_enabled: bool = True) -> None:
    """Pętla procesu roboczego: odbiera (źródło, katalog wyników), odsyła ("ok", skrót) albo ("error", opis)."""
    from ..output import load_unknown_mode
    from ..pipeline import Pipeline

    pipeline = None
    unknown_mode = None
    while True:
        try:
            job = conn.recv()
        except (EOFError, OSError):         # serwer zakończył pracę
            return
        if job is None:
            return
        source, out_dir = job
        try:
            if pipeline is None:
                pipeline = Pipeline() if ocr_enabled else Pipeline(ocr_engine=None)
                unknown_mode = load_unknown_mode()
            conn.send(("ok", process_document(pipeline, source, out_dir, unknown_mode)))
        except Exception as exc:  # błąd jednego dokumentu nie kończy procesu roboczego
            conn.send(("error", f"{type(exc).__name__}: {exc}"))


class ProcessRunner:
    """Jeden proces roboczy uruchamiany przy pierwszym zadaniu i odtwarzany po awarii lub przekroczeniu czasu."""

    def __init__(self, target: Callable = worker_main, args: tuple = ()):
        self._ctx = multiprocessing.get_context("spawn")
        self._target, self._args = target, args
        self._proc = None
        self._conn = None

    def _start(self) -> None:
        parent, child = self._ctx.Pipe()
        proc = self._ctx.Process(target=self._target, args=(child, *self._args), daemon=True,
                                 name="odwolania2json-worker")
        proc.start()
        child.close()
        self._proc, self._conn = proc, parent

    def run(self, job: tuple, timeout: float) -> tuple[str, object]:
        """Zwraca ("ok", wynik) | ("error", opis) | ("timeout", None) | ("crash", None)."""
        if self._proc is None or not self._proc.is_alive():
            self._stop(force=True)
            self._start()
        try:
            self._conn.send(job)
            if not self._conn.poll(timeout):
                self._stop(force=True)
                return "timeout", None
            return self._conn.recv()
        except (EOFError, OSError, BrokenPipeError):
            self._stop(force=True)
            return "crash", None

    def _stop(self, force: bool = False) -> None:
        proc, conn = self._proc, self._conn
        self._proc = self._conn = None
        if conn is not None:
            try:
                if not force:
                    conn.send(None)
            except OSError:
                pass
            conn.close()
        if proc is not None:
            proc.join(0 if force else 5)
            if proc.is_alive():
                proc.terminate()
                proc.join(5)
            if proc.is_alive():
                proc.kill()
                proc.join()

    def close(self) -> None:
        """Kończy proces roboczy (także w trakcie przetwarzania — wywoływane przy zamykaniu serwera)."""
        proc = self._proc
        if proc is not None and proc.is_alive():
            proc.terminate()
            proc.join(5)
            if proc.is_alive():
                proc.kill()


# ---------------------------------------------------------------------------------------------- kolejka
class ProcessingQueue:
    def __init__(self, store: DocumentStore, runner_factory: Callable[[], ProcessRunner] = ProcessRunner,
                 timeout_fn: Callable[[int | None], float] = timeout_for):
        self.store = store
        self._runner_factory = runner_factory
        self._timeout_fn = timeout_fn
        self._runner = None
        self._queue: collections.deque[str] = collections.deque()
        self._current: str | None = None
        self._cv = threading.Condition()
        self._thread: threading.Thread | None = None
        self._stopped = False

    # -------------------------------------------------------------- API dla serwera
    def enqueue(self, ids: list[str]) -> tuple[list[str], list[dict]]:
        """Dopisuje dokumenty do kolejki. Zwraca (dodane, pominięte z powodem)."""
        queued, skipped = [], []
        with self._cv:
            if self._stopped:
                raise RuntimeError("kolejka zatrzymana")
            for doc_id in dict.fromkeys(ids):                          # bez powtórzeń, kolejność zachowana
                if doc_id in self._queue or doc_id == self._current:
                    skipped.append({"id": doc_id, "reason": "active",
                                    "message": "Dokument jest już w kolejce lub jest przetwarzany."})
                    continue
                try:
                    self.store.update(doc_id, status=STATUS_QUEUED, queued_at=_now(), started_at=None,
                                      finished_at=None, seconds=None, error=None, note=None)
                except KeyError:
                    skipped.append({"id": doc_id, "reason": "not_found",
                                    "message": "Dokument nie istnieje lub został usunięty."})
                    continue
                self._queue.append(doc_id)
                queued.append(doc_id)
            if queued:
                self._ensure_thread()
                self._cv.notify()
        return queued, skipped

    def positions(self) -> dict[str, int]:
        """Pozycja w kolejce (1 = następny) dla dokumentów oczekujących."""
        with self._cv:
            return {doc_id: i for i, doc_id in enumerate(self._queue, start=1)}

    def discard(self, doc_id: str) -> None:
        """Usuwa dokument z kolejki (np. po usunięciu dokumentu)."""
        with self._cv:
            try:
                self._queue.remove(doc_id)
            except ValueError:
                pass

    def shutdown(self) -> None:
        """Zatrzymuje kolejkę; dokumenty oczekujące i przetwarzany wracają do statusu `added`."""
        with self._cv:
            self._stopped = True
            pending, self._queue = list(self._queue), collections.deque()
            self._cv.notify_all()
        if self._runner is not None:
            self._runner.close()
        if self._thread is not None:
            self._thread.join(timeout=10)
        for doc_id in pending:
            try:
                self.store.update(doc_id, status=STATUS_ADDED, queued_at=None)
            except KeyError:
                pass

    # -------------------------------------------------------------- dyspozytor
    def _ensure_thread(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._loop, name="odwolania2json-dispatcher", daemon=True)
            self._thread.start()

    def _loop(self) -> None:
        while True:
            with self._cv:
                while not self._queue and not self._stopped:
                    self._cv.wait()
                if self._stopped:
                    return
                doc_id = self._current = self._queue.popleft()
            try:
                self._process(doc_id)
            except Exception:   # dyspozytor nie może się zatrzymać z powodu jednego dokumentu
                self._safe_fail(doc_id, MSG_ERROR)
            finally:
                with self._cv:
                    self._current = None

    def _process(self, doc_id: str) -> None:
        try:
            doc = self.store.update(doc_id, status=STATUS_PROCESSING, started_at=_now())
            source = self.store.source_path(doc_id)
            tmp = self.store.new_result_tmp(doc_id)
        except KeyError:
            return                                   # dokument usunięty, zanim przyszła jego kolej
        if self._runner is None:
            self._runner = self._runner_factory()
        limit = self._timeout_fn(doc.pages)
        started = time.perf_counter()
        kind, payload = self._runner.run((str(source), str(tmp)), limit)
        elapsed = round(time.perf_counter() - started, 3)

        if kind == "ok":
            summary = payload["summary"]
            self.store.install_result(doc_id, tmp)
            error = None
            if summary["processing_status"] == "error":
                error = MSG_UNREADABLE
            elif summary["validation_errors"]:
                error = MSG_REJECTED
            self.store.update(doc_id, status=STATUS_FAILED if error else STATUS_PROCESSED, finished_at=_now(),
                              seconds=payload.get("seconds", elapsed), error=error, summary=summary)
            return
        _remove_tree(tmp)
        if self._stopped:                  # serwer zamykany w trakcie przetwarzania — dokument wraca do oczekujących
            self.store.update(doc_id, status=STATUS_ADDED, note=INTERRUPTED_NOTE, queued_at=None, started_at=None)
            return
        message = {"timeout": MSG_TIMEOUT.format(limit=int(limit)), "crash": MSG_CRASH}.get(kind)
        if message is None:
            message = f"{MSG_ERROR} ({payload})"
        self.store.update(doc_id, status=STATUS_FAILED, finished_at=_now(), seconds=elapsed, error=message,
                          summary=None)

    def _safe_fail(self, doc_id: str, message: str) -> None:
        try:
            self.store.update(doc_id, status=STATUS_FAILED, finished_at=_now(), error=message)
        except Exception:
            pass


def _remove_tree(path: Path) -> None:
    import shutil
    shutil.rmtree(path, ignore_errors=True)
