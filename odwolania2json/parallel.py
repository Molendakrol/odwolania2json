"""Równoległe przetwarzanie wielu PDF-ów w trybie wsadowym (`run`).

Każdy proces roboczy buduje własny `Pipeline` z tej samej konfiguracji i przetwarza pojedyncze pliki
dokładnie tak jak tryb sekwencyjny — zmienia się tylko liczba dokumentów przetwarzanych jednocześnie.
Wyniki wracają w kolejności wejściowej, więc zapis i diagnostyka są identyczne jak przy jednym procesie.

- Największe pliki (zwykle skany wymagające OCR) startują pierwsze, żeby nie zostały na koniec kolejki.
- Tryb `spawn` na wszystkich systemach (PyMuPDF nie jest bezpieczny po `fork`).
- Jeśli pula procesów ulegnie awarii, pozostałe dokumenty są przetwarzane sekwencyjnie w procesie głównym.
"""

from __future__ import annotations

import multiprocessing
import os
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path
from typing import Callable

from .pipeline import DocumentResult, Pipeline

MAX_AUTO_WORKERS = 8
_PIPELINE: Pipeline | None = None


def default_workers() -> int:
    """Liczba procesów domyślnie: rdzenie CPU minus jeden (system zostaje responsywny), od 1 do 8."""
    n = os.cpu_count() or 1
    return max(1, min(n - 1 if n > 2 else n, MAX_AUTO_WORKERS))


def _init_worker(factory: Callable[[], Pipeline]) -> None:
    global _PIPELINE
    _PIPELINE = factory()


def _work(path: Path, base_dir: Path | None) -> DocumentResult:
    assert _PIPELINE is not None
    return _PIPELINE.process_file(path, base_dir)


def process_paths(paths: list[Path], base_dir: Path | None, factory: Callable[[], Pipeline], workers: int,
                  progress: bool = True) -> list[DocumentResult]:
    """Przetwarza pliki w `workers` procesach; zwraca wyniki w kolejności `paths`.

    `factory` musi dać się przekazać do innego procesu (funkcja modułu lub functools.partial)."""
    results: list[DocumentResult | None] = [None] * len(paths)
    if workers <= 1 or len(paths) <= 1:
        pipeline = factory()
        for i, p in enumerate(paths):
            results[i] = pipeline.process_file(p, base_dir)
            _report(progress, i + 1, len(paths), results[i])
        return results   # type: ignore[return-value]

    order = sorted(range(len(paths)), key=lambda i: _size(paths[i]), reverse=True)
    done = 0
    try:
        ctx = multiprocessing.get_context("spawn")
        with ProcessPoolExecutor(max_workers=min(workers, len(paths)), mp_context=ctx,
                                 initializer=_init_worker, initargs=(factory,)) as pool:
            futures = {pool.submit(_work, paths[i], base_dir): i for i in order}
            for fut in as_completed(futures):
                i = futures[fut]
                results[i] = fut.result()
                done += 1
                _report(progress, done, len(paths), results[i])
    except BrokenProcessPool:
        missing = [i for i, r in enumerate(results) if r is None]
        print(f"UWAGA: awaria procesu roboczego — {len(missing)} dokumentów zostanie przetworzonych sekwencyjnie",
              file=sys.stderr)
        pipeline = factory()
        for i in missing:
            results[i] = pipeline.process_file(paths[i], base_dir)
            done += 1
            _report(progress, done, len(paths), results[i])
    return results   # type: ignore[return-value]


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _report(enabled: bool, done: int, total: int, result: DocumentResult | None) -> None:
    if enabled and result is not None:
        print(f"[{done}/{total}] {result.relative_path} -> {result.doc_class}", file=sys.stderr, flush=True)
