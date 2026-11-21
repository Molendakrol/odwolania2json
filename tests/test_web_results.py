"""P13.4: szczegóły wyniku i pobieranie output.json przez lokalne API interfejsu."""

import http.client
import io
import json
from urllib.parse import unquote

import pytest

from odwolania2json.extract import FieldExtractor
from odwolania2json.pipeline import Pipeline
from odwolania2json.web.documents import DocumentStore
from odwolania2json.web.processing import process_document
from odwolania2json.web.server import content_disposition, download_name, make_server

from .conftest import ODWOLANIE_TEXT, PELNOMOCNICTWO_TEXT, PRZELEW_TEXT, UNKNOWN_TEXT, make_pdf
from .test_web_documents import request, upload
from .test_web_processing import fake_queue, process, start, stop, wait_done, wait_status

TEMPLATE_KEYS = {"odwolanie": "OdwolanieKIO", "pelnomocnictwo": "mocodawca", "przelew": "przelew_bankowy"}


def download(srv, doc_id):
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
    conn.request("GET", f"/api/documents/{doc_id}/output.json")
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    return resp, body


@pytest.fixture(scope="module")
def processed(tmp_path_factory):
    """Serwer z prawdziwym procesem roboczym i czterema przetworzonymi dokumentami (jeden na typ + unknown)."""
    tmp = tmp_path_factory.mktemp("p134")
    srv = start(make_server("127.0.0.1", 0, data_dir=tmp / "data"))
    texts = {"odwolanie": ODWOLANIE_TEXT, "pelnomocnictwo": PELNOMOCNICTWO_TEXT, "przelew": PRZELEW_TEXT,
             "unknown": UNKNOWN_TEXT}
    names = {"odwolanie": "Odwołanie KIO — sprawa 1.pdf", "pelnomocnictwo": "pelnomocnictwo.PDF",
             "przelew": "przelew.pdf", "unknown": "zaproszenie.pdf"}
    ids = {}
    for label, text in texts.items():
        content = make_pdf(tmp / "src" / f"{label}.pdf", [text]).read_bytes()
        ids[label] = upload(srv, names[label], content)[1]["id"]
    process(srv, list(ids.values()))
    docs = wait_done(srv, ids.values())
    yield srv, ids, names, docs
    stop(srv)


# --- pobieranie: dokładna treść output.json z dysku, nazwa powiązana z dokumentem ---------------------

@pytest.mark.parametrize("label", ["odwolanie", "pelnomocnictwo", "przelew", "unknown"])
def test_download_returns_exact_output_file(processed, label):
    srv, ids, names, docs = processed
    doc_id = ids[label]
    assert docs[doc_id]["status"] == "processed" and docs[doc_id]["summary"]["doc_class"] == label
    resp, body = download(srv, doc_id)
    assert resp.status == 200
    assert resp.getheader("Content-Type") == "application/json; charset=utf-8"
    assert body == (srv.store.result_dir(doc_id) / "output.json").read_bytes()      # bajt w bajt
    data = json.loads(body)
    if label == "unknown":
        assert body == b"{}\n" and data == {}
    else:
        assert TEMPLATE_KEYS[label] in data
    disposition = resp.getheader("Content-Disposition")
    assert disposition.startswith("attachment;")
    utf8_name = unquote(disposition.split("filename*=UTF-8''")[1])
    assert utf8_name == names[label][:-4] + ".json"                                 # <nazwa PDF>.json


def test_download_name_and_header_for_polish_names():
    assert download_name("przelew.pdf") == "przelew.json"
    assert download_name("SKAN.PDF") == "SKAN.json"
    assert download_name("bez_rozszerzenia") == "bez_rozszerzenia.json"
    header = content_disposition("Odwołanie „Łódź”.json")
    assert 'filename="Odwolanie Lodz.json"' in header                       # wersja ASCII dla starszych klientów
    assert unquote(header.split("filename*=UTF-8''")[1]) == "Odwołanie „Łódź”.json"
    assert 'filename="a_b.json"' in content_disposition('a"b.json')            # cudzysłów nie łamie nagłówka


def test_result_endpoint_matches_files_and_has_no_full_text(processed):
    srv, ids, _, _ = processed
    for label, doc_id in ids.items():
        status, res = request(srv, "GET", f"/api/documents/{doc_id}/result")
        assert status == 200
        result_dir = srv.store.result_dir(doc_id)
        assert res["output_text"] == (result_dir / "output.json").read_text(encoding="utf-8")
        assert res["diagnostics"] == json.loads((result_dir / "diagnostics.json").read_text(encoding="utf-8"))
        classification = res["diagnostics"]["result"]["classification"]
        assert classification["label"] == label and "reason" in classification and "signals" in classification
        assert "text" not in res["diagnostics"]["result"]                     # bez pełnego tekstu PDF
        assert res["document"]["id"] == doc_id and "sha256" not in res["document"]
    unknown = request(srv, "GET", f"/api/documents/{ids['unknown']}/result")[1]
    assert unknown["diagnostics"]["result"]["fields"] is None
    assert unknown["diagnostics"]["result"]["classification"]["reason"] == "insufficient_signals"


# --- przypadki szczególne: brak wyniku, w trakcie, błąd -----------------------------------------------

def test_not_processed_document_has_no_result(fake_server_factory):
    srv, pdf = fake_server_factory()
    doc_id = upload(srv, "nowy.pdf", pdf)[1]["id"]
    status, err = request(srv, "GET", f"/api/documents/{doc_id}/result")
    assert status == 404 and err["error"] == "no_result"
    resp, body = download(srv, doc_id)
    assert resp.status == 404 and json.loads(body)["error"] == "no_output"


def test_document_in_processing_has_no_result_yet(fake_server_factory):
    srv, pdf = fake_server_factory(timeout=30)
    doc_id = upload(srv, "hang.pdf", pdf)[1]["id"]
    process(srv, [doc_id])
    wait_status(srv, doc_id, "processing")
    assert request(srv, "GET", f"/api/documents/{doc_id}/result")[0] == 404
    assert download(srv, doc_id)[0].status == 404


def test_failed_without_output_has_no_download(fake_server_factory):
    srv, pdf = fake_server_factory(timeout=1)
    doc_id = upload(srv, "hang.pdf", pdf)[1]["id"]
    process(srv, [doc_id])
    d = wait_done(srv, [doc_id], timeout=30)[doc_id]
    assert d["status"] == "failed" and "limit czasu" in d["error"]
    assert request(srv, "GET", f"/api/documents/{doc_id}/result")[0] == 404
    assert download(srv, doc_id)[0].status == 404


def test_result_rejected_by_validation_shows_diagnostics_without_output(tmp_path, monkeypatch):
    original = FieldExtractor.extract

    def broken(self, label, pages):
        res = original(self, label, pages)
        res.validation_errors = ["$.przelew_bankowy.kwota: oczekiwano liczby"]
        return res

    monkeypatch.setattr(FieldExtractor, "extract", broken)
    content = make_pdf(tmp_path / "p.pdf", [PRZELEW_TEXT]).read_bytes()
    store = DocumentStore(tmp_path / "data")
    doc, _ = store.add("p.pdf", io.BytesIO(content), len(content))
    tmp = store.new_result_tmp(doc.id)
    payload = process_document(Pipeline(ocr_engine=None), store.source_path(doc.id), tmp, "empty_object")
    store.install_result(doc.id, tmp)
    store.update(doc.id, status="failed", error="Wynik nie przeszedł walidacji.", summary=payload["summary"])

    srv = start(make_server("127.0.0.1", 0, data_dir=tmp_path / "data", queue_factory=fake_queue()))
    try:
        status, res = request(srv, "GET", f"/api/documents/{doc.id}/result")
        assert status == 200 and res["output_text"] is None
        assert res["diagnostics"]["rejected"] == ["$.przelew_bankowy.kwota: oczekiwano liczby"]
        resp, body = download(srv, doc.id)
        assert resp.status == 404 and json.loads(body)["error"] == "no_output"
    finally:
        stop(srv)


@pytest.mark.parametrize("bad", ["0" * 32, "..%2F..%2Fetc", "abc", "%2e%2e"])
def test_download_invalid_or_missing_ids(fake_server_factory, bad):
    srv, _ = fake_server_factory()
    assert download(srv, bad)[0].status == 404


def test_detail_view_assets_are_served(fake_server_factory):
    """Widok szczegółów to część statycznego frontendu (bez zależności zewnętrznych)."""
    srv, _ = fake_server_factory()
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
    conn.request("GET", "/static/js/app.js")
    js = conn.getresponse().read().decode("utf-8")
    conn.close()
    for marker in ("Pobierz JSON", "Kopiuj JSON", "/output.json", "Diagnostyka przetwarzania", "navigator.clipboard"):
        assert marker in js
    assert "http://" not in js and "https://" not in js


@pytest.fixture
def fake_server_factory(tmp_path):
    servers = []

    def factory(timeout=3.0):
        srv = start(make_server("127.0.0.1", 0, data_dir=tmp_path / f"data{len(servers)}", queue_factory=fake_queue(timeout)))
        servers.append(srv)
        return srv, make_pdf(tmp_path / "src" / "a.pdf", [PRZELEW_TEXT]).read_bytes()

    yield factory
    for srv in servers:
        stop(srv)
