"""Lokalny serwer interfejsu graficznego Odwołania2JSON (wyłącznie biblioteka standardowa).

Serwuje statyczny frontend (`static/`) i proste JSON API. Korzysta z istniejących modułów aplikacji
jako biblioteki — nie zmienia logiki pipeline'u. Domyślnie nasłuchuje tylko na 127.0.0.1.

API:
    GET    /api/info                — informacje o systemie (z konfiguracji)
    GET    /api/documents           — lista dodanych dokumentów
    POST   /api/documents           — dodanie PDF (surowe ciało żądania; nazwa w nagłówku X-Filename, URL-encoded)
    DELETE /api/documents/{id}      — usunięcie dokumentu (409 w trakcie przetwarzania)
    POST   /api/documents/process   — przetworzenie wybranych dokumentów: {"ids": [...]} -> 202
    GET    /api/documents/{id}/result — wynik przetwarzania (diagnostyka + wygenerowany JSON)
    GET    /api/documents/{id}/output.json — pobranie zapisanego pliku output.json (nazwa: <nazwa PDF>.json)

Żądania zmieniające stan wymagają nagłówka `X-Requested-With: odwolania2json` oraz nagłówka Host wskazującego
na localhost — przeglądarka nie wyśle ich z obcej strony bez zgody CORS (ochrona przed CSRF i DNS rebinding).
"""

from __future__ import annotations

import json
import mimetypes
import re
import unicodedata
import tomllib
from functools import lru_cache
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from .documents import DEFAULT_MAX_BYTES, ID_RE, DocumentBusy, DocumentStore, UploadError
from .processing import DIAGNOSTICS_FILE, OUTPUT_FILE, ProcessingQueue

from .. import __version__
from ..classify import DEFAULT_RULES_PATH, RuleClassifier
from ..extract import EXTRACTION_CONFIG, load_template
from ..ocr import engine_from_settings, load_ocr_settings
from ..output import load_unknown_mode

STATIC_DIR = Path(__file__).resolve().parent / "static"
CSRF_HEADER = ("X-Requested-With", "odwolania2json")
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}
STATUS_LABELS = {
    "added": "Dodano — oczekuje na przetwarzanie",
    "queued": "W kolejce",
    "processing": "Przetwarzanie",
    "processed": "Przetworzono",
    "failed": "Błąd przetwarzania",
}
MAX_JSON_BODY = 64 * 1024
MAX_DISCARD_BYTES = 256 * 1024 * 1024   # ciało odrzuconego uploadu doczytywane tylko do tej wielkości
MAX_IDS = 1000
CONTENT_TYPES = {".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".html": "text/html; charset=utf-8", ".svg": "image/svg+xml", ".json": "application/json"}


@lru_cache(maxsize=1)
def system_info() -> dict:
    """Informacje o systemie odczytane z konfiguracji aplikacji (bez przetwarzania dokumentów)."""
    with open(DEFAULT_RULES_PATH, "rb") as fh:
        rules = tomllib.load(fh)
    classifier = RuleClassifier(rules)
    with open(EXTRACTION_CONFIG, "rb") as fh:
        extraction = tomllib.load(fh)

    settings = load_ocr_settings()
    engine = engine_from_settings(settings)
    ocr_available, ocr_detail = engine.is_available() if engine else (False, "OCR wyłączony w konfiguracji")

    classes = []
    for label in classifier.labels:
        template = load_template(label)
        classes.append({
            "label": label,
            "description": rules["classes"][label].get("description", ""),
            "template_keys": list(template.keys()),
        })
    return {
        "name": "Odwołania2JSON",
        "version": __version__,
        "processing": "lokalne (CPU), bez zewnętrznych API",
        "classes": classes,
        "unknown_label": "unknown",
        "unknown_output": "{}" if load_unknown_mode() == "empty_object" else "pusty plik",
        "ocr": {
            "enabled": settings.enabled,
            "available": ocr_available,
            "engine": engine.name if engine else None,
            "language": settings.language,
            "dpi": settings.dpi,
            "detail": None if ocr_available else ocr_detail,
        },
        "czynnosci": sorted(({"id": c["id"], "opis": c["opis"]} for c in extraction["czynnosc"]), key=lambda c: c["id"]),
        "vetoes": [v.get("description", v["name"]) for v in rules.get("veto", [])],
    }


_NO_DECOMPOSITION = str.maketrans({"ł": "l", "Ł": "L"})   # litery bez rozkładu NFKD


def download_name(original_name: str) -> str:
    """Nazwa pobieranego JSON-a: nazwa PDF z rozszerzeniem .json (jak w CLI: <plik>.pdf -> <plik>.json)."""
    stem = re.sub(r"\.pdf$", "", original_name, flags=re.IGNORECASE) or "wynik"
    return f"{stem}.json"


def content_disposition(filename: str) -> str:
    """Nagłówek załącznika: wersja ASCII (fallback) + pełna nazwa UTF-8 (RFC 6266 / RFC 5987)."""
    ascii_name = unicodedata.normalize("NFKD", filename.translate(_NO_DECOMPOSITION)).encode("ascii", "ignore").decode("ascii")
    ascii_name = re.sub(r"[^A-Za-z0-9._ -]", "_", ascii_name).strip() or "wynik.json"
    return f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(filename, safe="")}'


class Handler(BaseHTTPRequestHandler):
    server_version = "Odwolania2JSON-UI"

    def log_message(self, fmt: str, *args) -> None:  # cichy serwer; błędy zwracane w odpowiedziach
        pass

    def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: HTTPStatus, payload: dict) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _static(self, rel: str) -> None:
        target = (STATIC_DIR / rel).resolve()
        if STATIC_DIR not in target.parents or not target.is_file():
            self._json(HTTPStatus.NOT_FOUND, {"error": "nie znaleziono"})
            return
        ctype = CONTENT_TYPES.get(target.suffix) or mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self._send(HTTPStatus.OK, target.read_bytes(), ctype)

    # ------------------------------------------------------------------ dokumenty
    @property
    def store(self) -> DocumentStore:
        return self.server.store  # type: ignore[attr-defined]

    def _error(self, status: int, code: str, message: str) -> None:
        self._json(HTTPStatus(status), {"error": code, "message": message})

    def _mutation_allowed(self) -> bool:
        try:
            host = urlsplit("//" + (self.headers.get("Host") or "")).hostname
        except ValueError:
            host = None
        if host not in ALLOWED_HOSTS or self.headers.get(CSRF_HEADER[0]) != CSRF_HEADER[1]:
            self._error(403, "forbidden", "Żądanie odrzucone: dozwolone wyłącznie z interfejsu aplikacji na tym komputerze.")
            return False
        return True

    @property
    def queue(self) -> ProcessingQueue:
        return self.server.queue  # type: ignore[attr-defined]

    def _doc_payload(self, doc, positions: dict[str, int] | None = None) -> dict:
        data = dict(doc.to_dict(), status_label=STATUS_LABELS.get(doc.status, doc.status))
        data["queue_position"] = (positions or {}).get(doc.id)
        data.pop("sha256", None)
        return data

    def _documents_payload(self) -> dict:
        positions = self.queue.positions()
        docs = [self._doc_payload(d, positions) for d in self.store.list()]
        return {"documents": docs, "count": len(docs), "max_bytes": self.store.max_bytes}

    def _read_json_body(self) -> dict | None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if not 0 < length <= MAX_JSON_BODY:
            self.close_connection = True
            self._error(400, "bad_request", "Nieprawidłowe żądanie.")
            return None
        try:
            data = json.loads(self.rfile.read(length))
        except (ValueError, UnicodeDecodeError):
            data = None
        if not isinstance(data, dict):
            self._error(400, "bad_request", "Nieprawidłowe żądanie (oczekiwano obiektu JSON).")
            return None
        return data

    def _process_documents(self) -> None:
        data = self._read_json_body()
        if data is None:
            return
        ids = data.get("ids")
        if not isinstance(ids, list) or not ids or len(ids) > MAX_IDS or not all(isinstance(i, str) for i in ids):
            self._error(400, "bad_request", "Wybierz co najmniej jeden dokument do przetworzenia.")
            return
        valid = [i for i in ids if ID_RE.fullmatch(i)]
        queued, skipped = self.queue.enqueue(valid)
        skipped += [{"id": i, "reason": "not_found", "message": "Dokument nie istnieje lub został usunięty."}
                    for i in ids if not ID_RE.fullmatch(i)]
        self._json(HTTPStatus.ACCEPTED, {"queued": queued, "skipped": skipped})

    def _result(self, doc_id: str) -> None:
        try:
            doc = self.store.get(doc_id)
            result_dir = self.store.result_dir(doc_id)
        except KeyError:
            self._error(404, "not_found", "Dokument nie istnieje lub został usunięty.")
            return
        diag_path = result_dir / DIAGNOSTICS_FILE
        if not diag_path.is_file():
            self._error(404, "no_result", "Dokument nie został jeszcze przetworzony.")
            return
        output_path = result_dir / OUTPUT_FILE
        self._json(HTTPStatus.OK, {
            "document": self._doc_payload(doc, self.queue.positions()),
            "diagnostics": json.loads(diag_path.read_text(encoding="utf-8")),
            "output_text": output_path.read_text(encoding="utf-8") if output_path.is_file() else None,
        })

    def _download_output(self, doc_id: str) -> None:
        """Zwraca dokładnie zapisany plik output.json (bez ponownej serializacji)."""
        try:
            doc = self.store.get(doc_id)
            output_path = self.store.result_dir(doc_id) / OUTPUT_FILE
        except KeyError:
            self._error(404, "not_found", "Dokument nie istnieje lub został usunięty.")
            return
        if not output_path.is_file():
            self._error(404, "no_output", "Dla tego dokumentu nie zapisano wyniku JSON.")
            return
        body = output_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Disposition", content_disposition(download_name(doc.original_name)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _discard_body(self, length: int) -> None:
        """Odczytuje i odrzuca ciało żądania (maks. MAX_DISCARD_BYTES, z limitem czasu gniazda)."""
        if length > MAX_DISCARD_BYTES:
            return
        remaining = length
        try:
            self.connection.settimeout(10)
            while remaining > 0:
                chunk = self.rfile.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
        except OSError:
            pass

    def do_POST(self) -> None:  # noqa: N802
        path = unquote(urlsplit(self.path).path)
        if path not in ("/api/documents", "/api/documents/process"):
            self._error(404, "not_found", "Nie znaleziono.")
            return
        if not self._mutation_allowed():
            self.close_connection = True
            return
        if path == "/api/documents/process":
            self._process_documents()
            return
        try:
            length = int(self.headers["Content-Length"]) if self.headers.get("Content-Length") else None
        except ValueError:
            length = None
        filename = unquote(self.headers.get("X-Filename", ""))
        try:
            doc, duplicates = self.store.add(filename, self.rfile, length)
        except UploadError as exc:
            if exc.code in ("too_large", "length", "extension") and length:
                # Ciało odrzuconego żądania nie zostało przeczytane. Doczytujemy je (do limitu), żeby klient
                # otrzymał czytelną odpowiedź zamiast zerwanego połączenia; większych nie czytamy.
                self.close_connection = True
                self._discard_body(length)
            self._error(exc.http_status, exc.code, exc.message)
            return
        except OSError:
            self.close_connection = True
            self._error(500, "storage", "Nie udało się zapisać pliku na dysku.")
            return
        payload = dict(doc.to_dict(), status_label=STATUS_LABELS.get(doc.status, doc.status), duplicate_of=duplicates)
        payload.pop("sha256", None)
        self._json(HTTPStatus.CREATED, payload)

    def do_DELETE(self) -> None:  # noqa: N802
        path = unquote(urlsplit(self.path).path)
        prefix = "/api/documents/"
        if not path.startswith(prefix):
            self._error(404, "not_found", "Nie znaleziono.")
            return
        if not self._mutation_allowed():
            return
        doc_id = path[len(prefix):]
        try:
            self.store.delete(doc_id)
        except KeyError:
            self._error(404, "not_found", "Dokument nie istnieje lub został już usunięty.")
            return
        except DocumentBusy:
            self._error(409, "busy", "Dokument jest właśnie przetwarzany. Usuń go po zakończeniu przetwarzania.")
            return
        self.queue.discard(doc_id)
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802 (nazwa wymagana przez http.server)
        path = unquote(urlsplit(self.path).path)
        if path == "/api/documents":
            self._json(HTTPStatus.OK, self._documents_payload())
            return
        if path.startswith("/api/documents/") and path.endswith("/result"):
            self._result(path[len("/api/documents/"):-len("/result")])
            return
        if path.startswith("/api/documents/") and path.endswith("/output.json"):
            self._download_output(path[len("/api/documents/"):-len("/output.json")])
            return
        if path in ("/", "/index.html"):
            self._static("index.html")
        elif path.startswith("/static/"):
            self._static(path[len("/static/"):])
        elif path == "/api/info":
            try:
                self._json(HTTPStatus.OK, system_info())
            except Exception as exc:  # błąd konfiguracji nie może zatrzymać serwera
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"{type(exc).__name__}: {exc}"})
        else:
            self._json(HTTPStatus.NOT_FOUND, {"error": "nie znaleziono"})


class AppServer(ThreadingHTTPServer):
    daemon_threads = True

    def server_close(self) -> None:
        super().server_close()
        queue = getattr(self, "queue", None)
        if queue is not None:
            queue.shutdown()           # kończy proces roboczy; przerwane dokumenty wracają do oczekujących


def make_server(host: str = "127.0.0.1", port: int = 8000, data_dir: Path | str | None = None,
                max_bytes: int = DEFAULT_MAX_BYTES, queue_factory=ProcessingQueue) -> AppServer:
    server = AppServer((host, port), Handler)
    server.store = DocumentStore(data_dir, max_bytes)  # type: ignore[attr-defined]
    server.store.recover_interrupted()
    server.queue = queue_factory(server.store)  # type: ignore[attr-defined]
    return server
