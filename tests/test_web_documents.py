"""P13.2: dodawanie, listowanie i usuwanie dokumentów PDF przez lokalne API interfejsu."""

import http.client
import json
import threading
from pathlib import Path
from urllib.parse import quote

import pymupdf
import pytest

from odwolania2json.web import documents as documents_module
from odwolania2json.web.documents import DocumentStore, UploadError, safe_display_name
from odwolania2json.web.server import make_server

from .conftest import PRZELEW_TEXT, make_pdf

OK_HEADERS = {"X-Requested-With": "odwolania2json"}


@pytest.fixture
def server(tmp_path):
    srv = make_server("127.0.0.1", 0, data_dir=tmp_path / "data", max_bytes=200_000)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def pdf_bytes(tmp_path):
    return make_pdf(tmp_path / "src" / "dokument.pdf", [PRZELEW_TEXT, "Druga strona"]).read_bytes()


def request(srv, method, path, body=None, headers=None, host=None):
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
    hdrs = dict(headers or {})
    if host:
        conn.putrequest(method, path, skip_host=True)
        conn.putheader("Host", host)
        for k, v in hdrs.items():
            conn.putheader(k, v)
        conn.putheader("Content-Length", str(len(body or b"")))
        conn.endheaders(body or b"")
    else:
        conn.request(method, path, body=body, headers=hdrs)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    payload = json.loads(data) if data and resp.getheader("Content-Type", "").startswith("application/json") else None
    return resp.status, payload


def upload(srv, name, content, headers=OK_HEADERS):
    return request(srv, "POST", "/api/documents", content, {**headers, "X-Filename": quote(name)})


def stored_files(srv):
    root = srv.store.root
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()) if root.exists() else []


# --- dodawanie -----------------------------------------------------------------------------------

def test_valid_pdf_is_stored_outside_project_with_metadata(server, pdf_bytes):
    status, doc = upload(server, "Odwołanie sprawy 1.pdf", pdf_bytes)
    assert status == 201
    assert doc["original_name"] == "Odwołanie sprawy 1.pdf" and doc["size"] == len(pdf_bytes) and doc["pages"] == 2
    assert doc["status"] == "added" and doc["status_label"] == "Dodano — oczekuje na przetwarzanie"
    assert "sha256" not in doc and doc["duplicate_of"] == []
    assert len(doc["id"]) == 32
    assert stored_files(server) == [f"{doc['id']}/meta.json", f"{doc['id']}/source.pdf"]
    assert (server.store.root / doc["id"] / "source.pdf").read_bytes() == pdf_bytes
    assert Path(__file__).parents[1] not in server.store.root.parents    # poza katalogiem projektu


def test_empty_file_is_rejected(server):
    status, err = upload(server, "pusty.pdf", b"")
    assert status == 400 and err["error"] == "empty" and err["message"] == "Plik jest pusty."
    assert stored_files(server) == []


def test_non_pdf_with_pdf_extension_is_rejected(server):
    status, err = upload(server, "faktura.pdf", b"PK\x03\x04 to jest archiwum zip, nie PDF" * 10)
    assert status == 422 and err["error"] == "not_pdf"
    assert stored_files(server) == []


def test_wrong_extension_is_rejected_even_for_real_pdf(server, pdf_bytes):
    status, err = upload(server, "dokument.docx", pdf_bytes)
    assert status == 415 and err["error"] == "extension"


def test_size_limit(server):
    big = b"%PDF-1.7\n" + b"0" * 210_000
    status, err = upload(server, "duzy.pdf", big)
    assert status == 413 and err["error"] == "too_large" and "MB" in err["message"]
    assert stored_files(server) == []


def test_corrupted_pdf_is_rejected(server):
    status, err = upload(server, "uszkodzony.pdf", b"%PDF-1.7\n to nie jest poprawna struktura PDF " * 20)
    assert status == 422 and err["error"] == "corrupted"


def test_encrypted_pdf_is_rejected(server, tmp_path):
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "tajne")
    enc = tmp_path / "enc.pdf"
    doc.save(enc, encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="o", user_pw="u")
    status, err = upload(server, "zaszyfrowany.pdf", enc.read_bytes())
    assert status == 422 and err["error"] == "encrypted"


def test_multiple_documents_and_listing(server, pdf_bytes, tmp_path):
    other = make_pdf(tmp_path / "b.pdf", ["Inny dokument"]).read_bytes()
    ids = [upload(server, f"plik_{i}.pdf", content)[1]["id"] for i, content in enumerate([pdf_bytes, other, pdf_bytes])]
    assert len(set(ids)) == 3
    status, data = request(server, "GET", "/api/documents")
    assert status == 200 and data["count"] == 3 and data["max_bytes"] == 200_000
    assert [d["id"] for d in data["documents"]] == ids                       # kolejność dodania
    assert [d["original_name"] for d in data["documents"]] == ["plik_0.pdf", "plik_1.pdf", "plik_2.pdf"]
    assert all("sha256" not in d for d in data["documents"])


def test_same_content_twice_creates_separate_documents_and_reports_duplicate(server, pdf_bytes):
    first = upload(server, "a.pdf", pdf_bytes)[1]
    second = upload(server, "a.pdf", pdf_bytes)[1]
    assert first["id"] != second["id"] and second["duplicate_of"] == [first["id"]]
    assert (server.store.root / first["id"] / "source.pdf").read_bytes() == pdf_bytes   # pierwszy nienaruszony


def test_id_collision_never_overwrites_existing_document(tmp_path, pdf_bytes, monkeypatch):
    store = DocumentStore(tmp_path)
    monkeypatch.setattr(documents_module.secrets, "token_hex", lambda n: "a" * 32)
    import io
    store.add("pierwszy.pdf", io.BytesIO(pdf_bytes), len(pdf_bytes))
    with pytest.raises(FileExistsError):
        store.add("drugi.pdf", io.BytesIO(b"%PDF-1.4 inna" + pdf_bytes[13:]), len(pdf_bytes))
    assert store.get("a" * 32).original_name == "pierwszy.pdf"
    assert (store.root / ("a" * 32) / "source.pdf").read_bytes() == pdf_bytes
    assert not list(store.root.glob(".upload-*"))                            # plik tymczasowy sprzątnięty


# --- usuwanie ------------------------------------------------------------------------------------

def test_delete_document(server, pdf_bytes):
    keep = upload(server, "zostaje.pdf", pdf_bytes)[1]
    gone = upload(server, "usun.pdf", pdf_bytes)[1]
    status, _ = request(server, "DELETE", f"/api/documents/{gone['id']}", headers=OK_HEADERS)
    assert status == 204
    assert [d["id"] for d in request(server, "GET", "/api/documents")[1]["documents"]] == [keep["id"]]
    assert not (server.store.root / gone["id"]).exists()
    status, err = request(server, "DELETE", f"/api/documents/{gone['id']}", headers=OK_HEADERS)
    assert status == 404 and err["error"] == "not_found"


@pytest.mark.parametrize("doc_id", ["0" * 32, "nie-ma", "ABCDEF" * 6, ""])
def test_delete_nonexistent_or_invalid_id(server, doc_id):
    status, err = request(server, "DELETE", f"/api/documents/{doc_id}", headers=OK_HEADERS)
    assert status == 404 and err["error"] == "not_found"


# --- bezpieczeństwo ------------------------------------------------------------------------------

@pytest.mark.parametrize("path", [
    "/api/documents/../../etc", "/api/documents/%2e%2e%2f%2e%2e%2fdata", "/api/documents/..%2F..%2Fx",
])
def test_delete_path_traversal_is_rejected(server, pdf_bytes, path):
    doc = upload(server, "a.pdf", pdf_bytes)[1]
    status, _ = request(server, "DELETE", path, headers=OK_HEADERS)
    assert status == 404
    assert (server.store.root / doc["id"] / "source.pdf").exists()
    assert server.store.root.parent.exists()


@pytest.mark.parametrize(("raw", "safe"), [
    ("../../../etc/passwd.pdf", "passwd.pdf"),
    ("..\\..\\Windows\\system32\\x.pdf", "x.pdf"),
    ("/abs/path/dokument.pdf", "dokument.pdf"),
    ("nazwa\x00\x1b[31mzła.pdf", "nazwa[31mzła.pdf"),
    ("..pdf", "pdf"),
])
def test_safe_display_names(raw, safe):
    assert safe_display_name(raw) == safe


def test_traversal_filename_is_stored_only_as_metadata(server, pdf_bytes, tmp_path):
    status, doc = upload(server, "../../poza_magazynem.pdf", pdf_bytes)
    assert status == 201 and doc["original_name"] == "poza_magazynem.pdf"
    assert stored_files(server) == [f"{doc['id']}/meta.json", f"{doc['id']}/source.pdf"]
    assert not (tmp_path / "poza_magazynem.pdf").exists() and not (server.store.root.parent / "poza_magazynem.pdf").exists()


def test_long_filename_is_truncated(server, pdf_bytes):
    status, doc = upload(server, "a" * 400 + ".pdf", pdf_bytes)
    assert status == 201 and len(doc["original_name"]) == 200


def test_mutations_require_app_header(server, pdf_bytes):
    status, err = upload(server, "a.pdf", pdf_bytes, headers={})
    assert status == 403 and err["error"] == "forbidden"
    doc = upload(server, "a.pdf", pdf_bytes)[1]
    status, _ = request(server, "DELETE", f"/api/documents/{doc['id']}")
    assert status == 403
    assert len(request(server, "GET", "/api/documents")[1]["documents"]) == 1


def test_foreign_host_header_is_rejected(server, pdf_bytes):
    """Ochrona przed DNS rebinding: żądanie z nagłówkiem Host innej domeny jest odrzucane."""
    status, err = request(server, "POST", "/api/documents", pdf_bytes,
                          {**OK_HEADERS, "X-Filename": "a.pdf"}, host="zla-strona.example:8000")
    assert status == 403
    assert stored_files(server) == []


def test_upload_error_messages_are_polish():
    err = UploadError("empty", "Plik jest pusty.")
    assert err.http_status == 400 and err.message == "Plik jest pusty."
