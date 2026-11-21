"""Lokalny magazyn dokumentów PDF dodanych przez interfejs.

Układ na dysku (poza repozytorium projektu):
    <katalog_danych>/documents/<id>/source.pdf
    <katalog_danych>/documents/<id>/meta.json
    <katalog_danych>/documents/<id>/result/       (P13.3: output.json + diagnostics.json po przetworzeniu)

- `id` to 32 losowe znaki szesnastkowe (secrets); ścieżki zapisu nigdy nie pochodzą od nazwy pliku użytkownika,
  oryginalna nazwa jest przechowywana wyłącznie jako metadane.
- Katalog dokumentu tworzony jest wyłącznie jako nowy (`mkdir` bez `exist_ok`) — brak nadpisywania.
- Zapis przez plik tymczasowy i atomowe przeniesienie; niepełny upload nie zostawia dokumentu.
- `source.pdf` po zapisaniu jest tylko do odczytu; przetwarzanie nigdy go nie zmienia.
- Status przetwarzania i skrót wyniku są w `meta.json` (zapis atomowy). Wyniki trafiają do `result/`
  dopiero po zakończeniu przetwarzania (podmiana całego katalogu).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import sys
import tempfile
import threading
import time
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

import pymupdf

DEFAULT_MAX_BYTES = 50 * 1024 * 1024          # limit pojedynczego pliku (zmiana: --max-upload-mb)
PDF_HEADER_WINDOW = 1024                       # specyfikacja PDF dopuszcza nagłówek w pierwszych 1024 bajtach
ID_RE = re.compile(r"^[0-9a-f]{32}$")
STATUS_ADDED = "added"            # dodany, oczekuje na przetwarzanie
STATUS_QUEUED = "queued"          # w kolejce przetwarzania
STATUS_PROCESSING = "processing"  # przetwarzany przez proces roboczy
STATUS_PROCESSED = "processed"    # przetworzony (także gdy typ = unknown)
STATUS_FAILED = "failed"          # błąd przetwarzania
ACTIVE_STATUSES = (STATUS_QUEUED, STATUS_PROCESSING)
INTERRUPTED_NOTE = "Poprzednie przetwarzanie zostało przerwane (aplikacja została zamknięta)."
RESULT_DIR = "result"
_PDF_LOCK = threading.Lock()      # MuPDF nie jest bezpieczny wątkowo — jedno otwarcie PDF naraz w procesie serwera
CHUNK = 64 * 1024


class DocumentBusy(RuntimeError):
    """Operacja niedozwolona, bo dokument jest właśnie przetwarzany."""


class UploadError(ValueError):
    """Błąd walidacji przesyłanego pliku; `message` jest zrozumiały dla użytkownika (po polsku)."""

    def __init__(self, code: str, message: str, http_status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


def default_data_dir() -> Path:
    """Katalog danych użytkownika poza projektem (nadpisanie: zmienna ODWOLANIA_DATA_DIR)."""
    if env := os.environ.get("ODWOLANIA_DATA_DIR"):
        return Path(env)
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "Odwolania2JSON"


def safe_display_name(raw: str | None) -> str:
    """Nazwa do wyświetlania: tylko ostatni człon ścieżki, bez znaków sterujących, maks. 200 znaków."""
    name = unicodedata.normalize("NFC", raw or "")
    name = re.split(r"[\\/]", name)[-1]
    name = "".join(ch for ch in name if unicodedata.category(ch)[0] != "C").strip().strip(".")
    if len(name) > 200:                         # skracanie z zachowaniem rozszerzenia
        stem, dot, ext = name.rpartition(".")
        name = (stem[: 200 - len(ext) - 1] + dot + ext) if dot and len(ext) <= 10 else name[:200]
    return name or "dokument.pdf"


@dataclass
class Document:
    id: str
    original_name: str
    size: int
    pages: int | None
    sha256: str
    added_at: str
    added_ns: int = 0                     # znacznik czasu do stabilnego sortowania (kolejność dodania)
    status: str = STATUS_ADDED
    queued_at: str | None = None          # P13.3: przebieg przetwarzania
    started_at: str | None = None
    finished_at: str | None = None
    seconds: float | None = None
    error: str | None = None              # komunikat błędu (po polsku) dla statusu `failed`
    note: str | None = None               # informacja dodatkowa, np. o przerwanym przetwarzaniu
    summary: dict | None = None           # skrót wyniku ostatniego przetwarzania (typ, OCR, walidacja)

    def to_dict(self) -> dict:
        return asdict(self)


class DocumentStore:
    def __init__(self, data_dir: Path | str | None = None, max_bytes: int = DEFAULT_MAX_BYTES):
        self.root = (Path(data_dir) if data_dir else default_data_dir()).resolve() / "documents"
        self.max_bytes = max_bytes
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ ścieżki
    def _doc_dir(self, doc_id: str) -> Path:
        if not ID_RE.fullmatch(doc_id or ""):
            raise KeyError(doc_id)
        path = (self.root / doc_id).resolve()
        if path.parent != self.root:          # dodatkowa ochrona przed wyjściem poza magazyn
            raise KeyError(doc_id)
        return path

    def result_dir(self, doc_id: str) -> Path:
        return self._doc_dir(doc_id) / RESULT_DIR

    def source_path(self, doc_id: str) -> Path:
        path = self._doc_dir(doc_id) / "source.pdf"
        if not path.is_file():
            raise KeyError(doc_id)
        return path

    # ------------------------------------------------------------------ odczyt
    def get(self, doc_id: str) -> Document:
        meta = self._doc_dir(doc_id) / "meta.json"
        if not meta.is_file():
            raise KeyError(doc_id)
        return Document(**json.loads(meta.read_text(encoding="utf-8")))

    def list(self) -> list[Document]:
        if not self.root.is_dir():
            return []
        docs = []
        for entry in self.root.iterdir():
            if entry.is_dir() and ID_RE.fullmatch(entry.name) and (entry / "meta.json").is_file():
                try:
                    docs.append(self.get(entry.name))
                except (KeyError, ValueError, TypeError):
                    continue                    # uszkodzone metadane nie blokują listy
        return sorted(docs, key=lambda d: (d.added_ns, d.added_at, d.id))

    # ------------------------------------------------------------------ zapis
    def add(self, filename: str | None, stream: BinaryIO, length: int | None) -> tuple[Document, list[str]]:
        """Waliduje i zapisuje PDF. Zwraca (dokument, identyfikatory dokumentów o identycznej treści)."""
        name = safe_display_name(filename)
        if not name.lower().endswith(".pdf"):
            raise UploadError("extension", "Obsługiwane są wyłącznie pliki PDF (rozszerzenie .pdf).", 415)
        if length is None:
            raise UploadError("length", "Brak informacji o rozmiarze pliku.", 411)
        if length == 0:
            raise UploadError("empty", "Plik jest pusty.")
        if length > self.max_bytes:
            raise UploadError("too_large", f"Plik przekracza dopuszczalny rozmiar {self.max_bytes // (1024 * 1024)} MB.", 413)

        self.root.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        fd, tmp_name = tempfile.mkstemp(prefix=".upload-", suffix=".tmp", dir=self.root)
        tmp = Path(tmp_name)
        try:
            received = 0
            head = b""
            with os.fdopen(fd, "wb") as out:
                while received < length:
                    chunk = stream.read(min(CHUNK, length - received))
                    if not chunk:
                        break
                    received += len(chunk)
                    if len(head) < PDF_HEADER_WINDOW:
                        head += chunk[: PDF_HEADER_WINDOW - len(head)]
                    digest.update(chunk)
                    out.write(chunk)
            if received != length:
                raise UploadError("incomplete", "Przesyłanie pliku zostało przerwane. Spróbuj ponownie.")
            if b"%PDF-" not in head:
                raise UploadError("not_pdf", "Plik nie jest dokumentem PDF (nieprawidłowa zawartość).", 422)
            pages = self._inspect(tmp)

            doc = Document(
                id=secrets.token_hex(16), original_name=name, size=received, pages=pages,
                sha256=digest.hexdigest(), added_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                added_ns=time.time_ns(),
            )
            with self._lock:
                duplicates = [d.id for d in self.list() if d.sha256 == doc.sha256]
                target = self.root / doc.id
                target.mkdir()                                  # FileExistsError -> brak nadpisania
                os.replace(tmp, target / "source.pdf")
                (target / "source.pdf").chmod(0o444)            # oryginał tylko do odczytu
                _write_meta(target, doc)
            return doc, duplicates
        finally:
            tmp.unlink(missing_ok=True)

    @staticmethod
    def _inspect(path: Path) -> int:
        """Sprawdza, czy PDF da się otworzyć; zwraca liczbę stron."""
        try:
            with _PDF_LOCK, pymupdf.open(path, filetype="pdf") as doc:
                if doc.needs_pass:
                    raise UploadError("encrypted", "Plik PDF jest zabezpieczony hasłem i nie może zostać przetworzony.", 422)
                if doc.page_count == 0:
                    raise UploadError("no_pages", "Plik PDF nie zawiera żadnej strony.", 422)
                return doc.page_count
        except UploadError:
            raise
        except Exception as exc:
            raise UploadError("corrupted", "Plik PDF jest uszkodzony i nie może zostać odczytany.", 422) from exc

    # ------------------------------------------------------------------ przetwarzanie (P13.3)
    def update(self, doc_id: str, **changes) -> Document:
        """Atomowo zmienia pola metadanych dokumentu. KeyError, gdy dokument nie istnieje."""
        with self._lock:
            doc = self.get(doc_id)
            for key, value in changes.items():
                if not hasattr(doc, key) or key in ("id", "original_name", "size", "sha256", "added_at", "added_ns"):
                    raise AttributeError(key)
                setattr(doc, key, value)
            _write_meta(self._doc_dir(doc_id), doc)
            return doc

    def recover_interrupted(self) -> list[str]:
        """Po starcie serwera: dokumenty pozostawione w kolejce/przetwarzaniu wracają do statusu `added`."""
        recovered = []
        for doc in self.list():
            if doc.status in ACTIVE_STATUSES:
                self.update(doc.id, status=STATUS_ADDED, note=INTERRUPTED_NOTE, queued_at=None, started_at=None)
                recovered.append(doc.id)
        return recovered

    def new_result_tmp(self, doc_id: str) -> Path:
        """Pusty katalog tymczasowy na wyniki w katalogu dokumentu (ten sam system plików -> atomowa podmiana)."""
        return Path(tempfile.mkdtemp(prefix=".result-", dir=self._doc_dir(doc_id)))

    def install_result(self, doc_id: str, tmp_dir: Path) -> None:
        """Zastępuje katalog `result/` dokumentu gotowymi wynikami z `tmp_dir`."""
        with self._lock:
            target = self.result_dir(doc_id)
            tmp_dir = Path(tmp_dir)
            if tmp_dir.resolve().parent != target.parent or not target.parent.is_dir():
                raise KeyError(doc_id)
            old = None
            if target.exists():
                old = target.with_name(f".old-{secrets.token_hex(4)}")
                os.replace(target, old)
            os.replace(tmp_dir, target)
            if old is not None:
                shutil.rmtree(old, ignore_errors=True)

    def delete(self, doc_id: str) -> None:
        with self._lock:
            path = self._doc_dir(doc_id)
            if not path.is_dir():
                raise KeyError(doc_id)
            meta = path / "meta.json"
            if meta.is_file() and json.loads(meta.read_text(encoding="utf-8")).get("status") == STATUS_PROCESSING:
                raise DocumentBusy(doc_id)
            shutil.rmtree(path, onexc=_force_remove)


def _write_meta(doc_dir: Path, doc: Document) -> None:
    """Zapis meta.json przez plik tymczasowy + os.replace (brak częściowo zapisanych metadanych)."""
    fd, tmp = tempfile.mkstemp(prefix=".meta-", suffix=".tmp", dir=doc_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(doc.to_dict(), fh, ensure_ascii=False, indent=2)
        os.replace(tmp, doc_dir / "meta.json")
    finally:
        Path(tmp).unlink(missing_ok=True)


def _force_remove(func, path, _exc) -> None:
    """Usuwanie plików tylko do odczytu (Windows) przy kasowaniu dokumentu."""
    os.chmod(path, 0o666)
    func(path)
