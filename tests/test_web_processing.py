"""P13.3: przetwarzanie dokumentów z interfejsu — kolejka, proces roboczy, statusy, wyniki, API."""

import hashlib
import io
import json
import threading
import time

import pytest

from odwolania2json.output import write_outputs
from odwolania2json.pipeline import Pipeline
from odwolania2json.web.documents import INTERRUPTED_NOTE, DocumentStore
from odwolania2json.web.processing import (MSG_CRASH, MSG_REJECTED, MSG_UNREADABLE, ProcessingQueue, ProcessRunner,
                                           process_document)
from odwolania2json.web.server import make_server

from .conftest import ODWOLANIE_TEXT, PRZELEW_TEXT, UNKNOWN_TEXT, make_pdf
from .test_web_documents import OK_HEADERS, request, upload
from .web_workers import fake_worker

ACTIVE = ("queued", "processing")


def fake_queue(timeout=3.0):
    return lambda store: ProcessingQueue(store, runner_factory=lambda: ProcessRunner(target=fake_worker),
                                         timeout_fn=lambda pages: timeout)


def start(srv):
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    return srv


def stop(srv):
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def real_server(tmp_path):
    srv = start(make_server("127.0.0.1", 0, data_dir=tmp_path / "data"))
    yield srv
    stop(srv)


@pytest.fixture
def fake_server(tmp_path):
    srv = start(make_server("127.0.0.1", 0, data_dir=tmp_path / "data", queue_factory=fake_queue()))
    yield srv
    stop(srv)


@pytest.fixture
def pdfs(tmp_path):
    return {name: make_pdf(tmp_path / "src" / f"{name}.pdf", [text]).read_bytes()
            for name, text in [("odwolanie", ODWOLANIE_TEXT), ("przelew", PRZELEW_TEXT), ("inny", UNKNOWN_TEXT)]}


def process(srv, ids, headers=OK_HEADERS):
    body = json.dumps({"ids": ids}).encode()
    return request(srv, "POST", "/api/documents/process", body, {**headers, "Content-Type": "application/json"})


def docs_by_id(srv):
    return {d["id"]: d for d in request(srv, "GET", "/api/documents")[1]["documents"]}


def wait_done(srv, ids, timeout=90):
    deadline = time.time() + timeout
    while time.time() < deadline:
        docs = docs_by_id(srv)
        if all(i not in docs or docs[i]["status"] not in ACTIVE for i in ids):
            return docs
        time.sleep(0.1)
    raise AssertionError("przetwarzanie nie zakończyło się w czasie testu")


def wait_status(srv, doc_id, status, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if docs_by_id(srv)[doc_id]["status"] == status:
            return
        time.sleep(0.05)
    raise AssertionError(f"dokument nie osiągnął statusu {status}")


# --- funkcja robocza: ta sama logika i ten sam JSON co CLI ----------------------------------------

@pytest.mark.parametrize(("name", "label"), [("odwolanie", "odwolanie"), ("przelew", "przelew"), ("inny", "unknown")])
def test_process_document_matches_cli_output(tmp_path, pdfs, name, label):
    src = tmp_path / "in" / f"{name}.pdf"
    src.parent.mkdir()
    src.write_bytes(pdfs[name])
    pipeline = Pipeline(ocr_engine=None)
    out = tmp_path / "out"
    out.mkdir()
    payload = process_document(pipeline, src, out, "empty_object")
    summary = payload["summary"]
    assert summary["doc_class"] == label and summary["processing_status"] == "ok" and summary["output_written"]
    assert summary["text_source"] == "native" and summary["ocr_status"] == "not_needed"

    cli_out = tmp_path / "cli"
    write_outputs([pipeline.process_file(src)], cli_out, "empty_object")          # ścieżka CLI `run`
    assert (out / "output.json").read_text(encoding="utf-8") == (cli_out / f"{name}.json").read_text(encoding="utf-8")
    diag = json.loads((out / "diagnostics.json").read_text(encoding="utf-8"))
    assert diag["result"]["doc_class"] == label and "text" not in diag["result"]       # bez pełnego tekstu
    assert sorted(p.name for p in out.iterdir()) == ["diagnostics.json", "output.json"]
    assert src.read_bytes() == pdfs[name]


# --- pełny przebieg z prawdziwym procesem roboczym ---------------------------------------------

def test_end_to_end_processing_with_real_worker(real_server, pdfs):
    ids = {name: upload(real_server, f"{name}.pdf", content)[1]["id"] for name, content in pdfs.items()}
    hashes = {i: hashlib.sha256(real_server.store.source_path(i).read_bytes()).hexdigest() for i in ids.values()}
    status, data = process(real_server, list(ids.values()))
    assert status == 202 and data["queued"] == list(ids.values()) and data["skipped"] == []

    docs = wait_done(real_server, ids.values())
    for name, label in [("odwolanie", "odwolanie"), ("przelew", "przelew"), ("inny", "unknown")]:
        d = docs[ids[name]]
        assert d["status"] == "processed" and d["status_label"] == "Przetworzono" and d["error"] is None
        assert d["summary"]["doc_class"] == label and d["summary"]["ocr_status"] == "not_needed"
        assert d["started_at"] and d["finished_at"] and d["seconds"] is not None and d["queue_position"] is None

    status, res = request(real_server, "GET", f"/api/documents/{ids['inny']}/result")
    assert status == 200 and res["output_text"] == "{}\n" and res["document"]["status"] == "processed"
    status, res = request(real_server, "GET", f"/api/documents/{ids['przelew']}/result")
    assert "przelew_bankowy" in json.loads(res["output_text"])
    assert res["diagnostics"]["result"]["classification"]["label"] == "przelew"

    for doc_id, digest in hashes.items():                       # oryginały nienaruszone i tylko do odczytu
        src = real_server.store.source_path(doc_id)
        assert hashlib.sha256(src.read_bytes()).hexdigest() == digest and not src.stat().st_mode & 0o222


def test_unreadable_source_is_reported_as_failed(real_server, pdfs):
    doc_id = upload(real_server, "a.pdf", pdfs["przelew"])[1]["id"]
    src = real_server.store.source_path(doc_id)
    src.chmod(0o644)
    src.write_bytes(b"%PDF-1.7 uszkodzony po dodaniu")              # symulacja uszkodzenia pliku na dysku
    process(real_server, [doc_id])
    d = wait_done(real_server, [doc_id])[doc_id]
    assert d["status"] == "failed" and d["error"] == MSG_UNREADABLE and d["summary"]["doc_class"] == "unknown"
    assert request(real_server, "GET", f"/api/documents/{doc_id}/result")[1]["output_text"] == "{}\n"


def test_reprocessing_replaces_only_result_directory(fake_server, pdfs):
    doc_id = upload(fake_server, "a.pdf", pdfs["przelew"])[1]["id"]
    for _ in range(2):
        process(fake_server, [doc_id])
        assert wait_done(fake_server, [doc_id])[doc_id]["status"] == "processed"
    doc_dir = fake_server.store.root / doc_id
    assert sorted(p.name for p in doc_dir.iterdir()) == ["meta.json", "result", "source.pdf"]   # bez plików tymczasowych
    assert sorted(p.name for p in (doc_dir / "result").iterdir()) == ["diagnostics.json", "output.json"]


# --- awarie pojedynczego dokumentu nie zatrzymują pozostałych ------------------------------------

def test_timeout_crash_and_error_affect_only_their_document(tmp_path, pdfs):
    srv = start(make_server("127.0.0.1", 0, data_dir=tmp_path / "data", queue_factory=fake_queue(timeout=1.5)))
    try:
        names = ["hang.pdf", "ok1.pdf", "crash.pdf", "boom.pdf", "ok2.pdf"]
        ids = [upload(srv, n, pdfs["przelew"])[1]["id"] for n in names]
        process(srv, ids)
        docs = wait_done(srv, ids, timeout=60)
        st = {n: docs[i] for n, i in zip(names, ids)}
        assert st["hang.pdf"]["status"] == "failed" and "limit czasu" in st["hang.pdf"]["error"]
        assert st["crash.pdf"]["status"] == "failed" and st["crash.pdf"]["error"] == MSG_CRASH
        assert st["boom.pdf"]["status"] == "failed" and "awaria testowa" in st["boom.pdf"]["error"]
        assert st["ok1.pdf"]["status"] == st["ok2.pdf"]["status"] == "processed"
        for i in ids:
            assert srv.store.source_path(i).read_bytes() == pdfs["przelew"]
        failed_dir = srv.store.root / ids[0]
        assert sorted(p.name for p in failed_dir.iterdir()) == ["meta.json", "source.pdf"]       # brak wyników częściowych
    finally:
        stop(srv)


def test_rejected_by_validation_is_failed(tmp_path, pdfs, monkeypatch):
    from odwolania2json.extract import FieldExtractor
    original = FieldExtractor.extract

    def broken(self, label, pages):
        res = original(self, label, pages)
        res.validation_errors = ["$.przelew_bankowy.kwota: zły typ"]
        return res

    monkeypatch.setattr(FieldExtractor, "extract", broken)
    src = tmp_path / "p.pdf"
    src.write_bytes(pdfs["przelew"])
    out = tmp_path / "out"
    out.mkdir()
    summary = process_document(Pipeline(ocr_engine=None), src, out, "empty_object")["summary"]
    assert not summary["output_written"] and summary["validation_errors"]
    assert not (out / "output.json").exists()

    store = DocumentStore(tmp_path / "data")
    doc, _ = store.add("p.pdf", io.BytesIO(pdfs["przelew"]), len(pdfs["przelew"]))

    class Runner:
        def run(self, job, timeout):
            return "ok", {"seconds": 0.1, "summary": summary}

        def close(self):
            pass

    queue = ProcessingQueue(store, runner_factory=Runner)
    queue.enqueue([doc.id])
    deadline = time.time() + 10
    while store.get(doc.id).status in ACTIVE and time.time() < deadline:
        time.sleep(0.05)
    queue.shutdown()
    assert store.get(doc.id).status == "failed" and store.get(doc.id).error == MSG_REJECTED


# --- API -------------------------------------------------------------------------------------------

def test_process_api_validation(fake_server, pdfs):
    doc_id = upload(fake_server, "a.pdf", pdfs["przelew"])[1]["id"]
    assert process(fake_server, [doc_id], headers={})[0] == 403                          # bez nagłówka aplikacji
    assert process(fake_server, [])[0] == 400
    status, err = request(fake_server, "POST", "/api/documents/process", b"nie-json", OK_HEADERS)
    assert status == 400 and err["error"] == "bad_request"
    assert request(fake_server, "POST", "/api/documents/process", b"[1]", OK_HEADERS)[0] == 400
    status, data = process(fake_server, ["0" * 32, "../../etc", doc_id, doc_id])
    assert status == 202 and data["queued"] == [doc_id]
    assert sorted(s["reason"] for s in data["skipped"]) == ["not_found", "not_found"]
    assert docs_by_id(fake_server)[doc_id]["status"] != "added"


def test_result_endpoint_errors(fake_server, pdfs):
    doc_id = upload(fake_server, "a.pdf", pdfs["przelew"])[1]["id"]
    status, err = request(fake_server, "GET", f"/api/documents/{doc_id}/result")
    assert status == 404 and err["error"] == "no_result"
    for bad in ["0" * 32, "..%2F..", "abc"]:
        assert request(fake_server, "GET", f"/api/documents/{bad}/result")[0] == 404


def test_active_document_cannot_be_deleted_or_queued_twice(tmp_path, pdfs):
    srv = start(make_server("127.0.0.1", 0, data_dir=tmp_path / "data", queue_factory=fake_queue(timeout=3)))
    try:
        first = upload(srv, "hang.pdf", pdfs["przelew"])[1]["id"]
        second = upload(srv, "ok.pdf", pdfs["przelew"])[1]["id"]
        process(srv, [first, second])
        wait_status(srv, first, "processing")
        docs = docs_by_id(srv)
        assert docs[second]["status"] == "queued" and docs[second]["queue_position"] == 1
        assert docs[second]["status_label"] == "W kolejce"
        status, data = process(srv, [first, second])
        assert data["queued"] == [] and {s["reason"] for s in data["skipped"]} == {"active"}
        status, err = request(srv, "DELETE", f"/api/documents/{first}", headers=OK_HEADERS)
        assert status == 409 and err["error"] == "busy"
        assert request(srv, "DELETE", f"/api/documents/{second}", headers=OK_HEADERS)[0] == 204   # usunięty z kolejki
        docs = wait_done(srv, [first], timeout=20)
        assert docs[first]["status"] == "failed" and second not in docs
        assert request(srv, "DELETE", f"/api/documents/{first}", headers=OK_HEADERS)[0] == 204
    finally:
        stop(srv)


# --- restart i zamykanie serwera ---------------------------------------------------------------------

def test_interrupted_documents_are_recovered_on_start(tmp_path, pdfs):
    store = DocumentStore(tmp_path / "data")
    a, _ = store.add("a.pdf", io.BytesIO(pdfs["przelew"]), len(pdfs["przelew"]))
    b, _ = store.add("b.pdf", io.BytesIO(pdfs["przelew"]), len(pdfs["przelew"]))
    store.update(a.id, status="processing", started_at="2026-11-21T10:00:00+00:00")
    store.update(b.id, status="queued")
    srv = make_server("127.0.0.1", 0, data_dir=tmp_path / "data", queue_factory=fake_queue())
    try:
        for doc_id in (a.id, b.id):
            doc = srv.store.get(doc_id)
            assert doc.status == "added" and doc.note == INTERRUPTED_NOTE and doc.started_at is None
    finally:
        srv.server_close()


def test_shutdown_during_processing_returns_documents_to_waiting(tmp_path, pdfs):
    srv = start(make_server("127.0.0.1", 0, data_dir=tmp_path / "data", queue_factory=fake_queue(timeout=60)))
    first = upload(srv, "hang.pdf", pdfs["przelew"])[1]["id"]
    second = upload(srv, "ok.pdf", pdfs["przelew"])[1]["id"]
    process(srv, [first, second])
    wait_status(srv, first, "processing")
    started = time.time()
    stop(srv)
    assert time.time() - started < 15                                    # zawieszony OCR nie blokuje zamknięcia
    assert srv.store.get(first).status == "added" and srv.store.get(first).note == INTERRUPTED_NOTE
    assert srv.store.get(second).status == "added"


def test_update_protects_identity_fields(tmp_path, pdfs):
    store = DocumentStore(tmp_path)
    doc, _ = store.add("a.pdf", io.BytesIO(pdfs["przelew"]), len(pdfs["przelew"]))
    for field in ("id", "sha256", "original_name", "nieistniejace"):
        with pytest.raises(AttributeError):
            store.update(doc.id, **{field: "x"})
    with pytest.raises(KeyError):
        store.update("0" * 32, status="queued")
