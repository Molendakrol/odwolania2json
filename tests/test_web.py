"""Testy lokalnego serwera interfejsu (P13.1): pliki statyczne, /api/info, bezpieczeństwo ścieżek."""

import json
import threading
import urllib.error
import urllib.request

import pytest

from odwolania2json.web.server import make_server, system_info


@pytest.fixture(scope="module")
def base_url():
    server = make_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def get(url):
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), e.read()


def test_index_served(base_url):
    status, ctype, body = get(base_url + "/")
    assert status == 200 and ctype.startswith("text/html")
    html = body.decode("utf-8")
    assert "Odwołania2JSON" in html and 'lang="pl"' in html
    for route in ("pulpit", "dokumenty", "przetwarzanie", "wyniki", "informacje"):
        assert f'data-route="{route}"' in html


@pytest.mark.parametrize(("path", "ctype"), [
    ("/static/css/tokens.css", "text/css"), ("/static/css/app.css", "text/css"), ("/static/js/app.js", "text/javascript"),
])
def test_static_assets(base_url, path, ctype):
    status, got, body = get(base_url + path)
    assert status == 200 and got.startswith(ctype) and body


def test_frontend_has_no_external_resources(base_url):
    """Interfejs działa offline: bez CDN, zewnętrznych czcionek i skryptów."""
    for path in ("/", "/static/css/tokens.css", "/static/css/app.css", "/static/js/app.js"):
        text = get(base_url + path)[2].decode("utf-8")
        assert "http://" not in text and "https://" not in text, path


def test_api_info_reports_backend_configuration(base_url):
    status, ctype, body = get(base_url + "/api/info")
    assert status == 200 and ctype.startswith("application/json")
    info = json.loads(body)
    assert [c["label"] for c in info["classes"]] == ["odwolanie", "pelnomocnictwo", "przelew"]
    assert info["classes"][2]["template_keys"] == ["przelew_bankowy"]
    assert info["unknown_label"] == "unknown" and info["unknown_output"] == "{}"
    assert info["ocr"]["language"] == "pol" and isinstance(info["ocr"]["available"], bool)
    assert [c["id"] for c in info["czynnosci"]] == list(range(1, 13))


@pytest.mark.parametrize("path", [
    "/static/../server.py", "/static/%2e%2e/server.py", "/static/../../config/app.toml", "/static/nie_ma.css",
    "/api/nieznane", "/etc/passwd",
])
def test_paths_outside_static_are_not_served(base_url, path):
    status, ctype, body = get(base_url + path)
    assert status == 404 and b"def make_server" not in body and b"[output]" not in body


def test_system_info_is_json_serialisable():
    json.dumps(system_info())


def test_server_binds_to_localhost_by_default():
    server = make_server(port=0)
    try:
        assert server.server_address[0] == "127.0.0.1"
    finally:
        server.server_close()
