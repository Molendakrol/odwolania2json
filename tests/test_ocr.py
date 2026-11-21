"""Testy OCR jako fallbacku ekstrakcji tekstu.

Większość testów używa atrapy silnika (FakeOcr) — są deterministyczne i nie wymagają Tesseracta.
Testy z rzeczywistym OCR (oznaczone `real_ocr`) uruchamiają się tylko, gdy dostępny jest pol.traineddata.
"""

import json
from pathlib import Path

import pytest

from odwolania2json.cli import main
from odwolania2json.ocr import OcrSettings, PyMuPdfTesseract, engine_from_settings, find_tessdata, load_ocr_settings
from odwolania2json.output import write_outputs
from odwolania2json.pdf_text import ExtractionStatus, TextSource, extract_text
from odwolania2json.pipeline import Pipeline, ProcessingStatus

from .conftest import (
    OFFICIAL, ODWOLANIE_TEXT, PELNOMOCNICTWO_TEXT, PRZELEW_TEXT, UNKNOWN_TEXT, FakeOcr, make_pdf, make_scan_pdf,
)


# 1. PDF z warstwą tekstową — OCR nie jest uruchamiany ------------------------------------------

def test_text_pdf_does_not_run_ocr(tmp_path):
    ocr = FakeOcr({0: "nie powinno zostać użyte"})
    res = extract_text(make_pdf(tmp_path / "t.pdf", [ODWOLANIE_TEXT]), ocr)
    assert ocr.calls == []
    assert res.ocr.status == "not_needed" and not res.ocr.attempted
    assert res.text_source is TextSource.NATIVE and res.status is ExtractionStatus.OK


def test_official_text_pdfs_never_trigger_ocr():
    ocr = FakeOcr()
    for name in ("p01_odwolanie.pdf", "p01_pelnomocnictwo.pdf", "p01_potwierdzenie_przelewu.pdf", "p42_odwolanie.pdf"):
        assert extract_text(OFFICIAL / name, ocr).text_source is TextSource.NATIVE
    assert ocr.calls == []


# 2. skan — OCR jest uruchamiany ----------------------------------------------------------------

def test_scan_runs_ocr_and_uses_its_text(tmp_path):
    ocr = FakeOcr({0: PELNOMOCNICTWO_TEXT})
    res = extract_text(make_scan_pdf(tmp_path / "s.pdf", ["dowolna treść"]), ocr)
    assert ocr.calls == [0]
    assert res.native_status is ExtractionStatus.NO_TEXT
    assert res.ocr.attempted and res.ocr.reason == "no_text" and res.ocr.status == "ok"
    assert res.ocr.pages_requested == [1] and res.ocr.pages_processed == 1 and res.ocr.pages_with_text == 1
    assert res.text_source is TextSource.OCR and res.status is ExtractionStatus.OK
    assert "niniejszym udziela pełnomocnictwa" in res.text


# 3. brak tekstu i brak możliwości OCR ---------------------------------------------------------

def test_scan_with_ocr_unavailable_is_no_text_without_crash(tmp_path):
    res = extract_text(make_scan_pdf(tmp_path / "s.pdf", ["x"]), FakeOcr(available=False))
    assert res.status is ExtractionStatus.NO_TEXT and res.text_source is TextSource.NONE
    assert res.ocr.status == "unavailable" and not res.ocr.attempted and res.ocr.errors


def test_scan_with_ocr_disabled(tmp_path):
    res = extract_text(make_scan_pdf(tmp_path / "s.pdf", ["x"]), None)
    assert res.status is ExtractionStatus.NO_TEXT and res.ocr.status == "disabled" and res.ocr.reason == "no_text"


def test_ocr_failure_on_every_page_is_reported_not_raised(tmp_path):
    res = extract_text(make_scan_pdf(tmp_path / "s.pdf", ["a", "b"]), FakeOcr(fail_pages={0, 1}))
    assert res.ocr.status == "failed" and len(res.ocr.errors) == 2 and "strona 1" in res.ocr.errors[0]
    assert res.status is ExtractionStatus.NO_TEXT


def test_ocr_failure_on_one_page_keeps_other_pages(tmp_path):
    res = extract_text(make_scan_pdf(tmp_path / "s.pdf", ["a", "b"]), FakeOcr({0: ODWOLANIE_TEXT}, fail_pages={1}))
    assert res.ocr.status == "partial" and res.ocr.pages_processed == 1
    assert res.page_sources == ["ocr", "none"] and res.status is ExtractionStatus.OK


# 4. PDF wielostronicowy — kolejność stron zachowana ---------------------------------------------

def test_multipage_scan_preserves_page_order(tmp_path):
    texts = {0: "STRONA PIERWSZA " + "a" * 60, 1: "STRONA DRUGA " + "b" * 60, 2: "STRONA TRZECIA " + "c" * 60}
    ocr = FakeOcr(texts)
    res = extract_text(make_scan_pdf(tmp_path / "m.pdf", ["1", "2", "3"]), ocr)
    assert ocr.calls == [0, 1, 2]
    assert res.ocr.pages_requested == [1, 2, 3]
    assert [p.split()[1] for p in res.pages] == ["PIERWSZA", "DRUGA", "TRZECIA"]
    assert res.text.index("PIERWSZA") < res.text.index("DRUGA") < res.text.index("TRZECIA")


# 5. pusta strona ------------------------------------------------------------------------------

def test_blank_page_in_text_document_is_not_ocred(tmp_path):
    ocr = FakeOcr()
    res = extract_text(make_pdf(tmp_path / "b.pdf", [ODWOLANIE_TEXT, ""]), ocr)
    assert ocr.calls == [] and res.ocr.status == "not_needed"   # pusta strona bez obrazu nie jest skanem


def test_blank_scanned_page_gives_no_text(tmp_path):
    res = extract_text(make_scan_pdf(tmp_path / "b.pdf", [""]), FakeOcr({0: ""}))
    assert res.ocr.status == "ok" and res.ocr.pages_with_text == 0
    assert res.status is ExtractionStatus.NO_TEXT and res.text_source is TextSource.NONE


# 6. OCR zwraca tekst niewystarczający ---------------------------------------------------------

def test_ocr_with_insufficient_text_stays_unknown(tmp_path):
    pdf = make_scan_pdf(tmp_path / "i.pdf", ["x"])
    pipe = Pipeline(ocr_engine=FakeOcr({0: "~~ ODWOŁANIE ~~ nieczytelne"}))
    res = pipe.process_file(pdf)
    assert res.extraction.status is ExtractionStatus.LOW_TEXT
    assert res.doc_class == "unknown" and res.classification.reason == "no_text"
    assert res.status is ProcessingStatus.NO_TEXT and res.fields is None


# 7–9. integracja z klasyfikatorem, ekstraktorami i zapisem ------------------------------------

@pytest.mark.parametrize(("text", "label"), [
    (ODWOLANIE_TEXT, "odwolanie"), (PELNOMOCNICTWO_TEXT, "pelnomocnictwo"), (PRZELEW_TEXT, "przelew"),
])
def test_scan_after_ocr_is_classified_and_extracted(tmp_path, text, label):
    pdf = make_scan_pdf(tmp_path / "scan.pdf", ["x"])
    res = Pipeline(ocr_engine=FakeOcr({0: text})).process_file(pdf)
    assert res.doc_class == label and res.extraction.text_source is TextSource.OCR
    assert res.fields is not None and res.fields.validation_errors == []


def test_scanned_appeal_json_content(tmp_path):
    pdf = make_scan_pdf(tmp_path / "case1" / "skan.pdf", ["x"])
    res = Pipeline(ocr_engine=FakeOcr({0: ODWOLANIE_TEXT})).process_file(pdf, tmp_path)
    report = write_outputs([res], tmp_path / "output")
    data = json.loads((tmp_path / "output" / "case1" / "skan.json").read_text(encoding="utf-8"))
    assert report.written and data["OdwolanieKIO"]["miejscowosc"] == "Szczecin"
    assert data["Odwolujacy"][0]["nazwa"] == "Testbud Sp. z o.o."


def test_scan_after_ocr_that_is_unrelated_stays_unknown(tmp_path):
    pdf = make_scan_pdf(tmp_path / "u.pdf", ["x"])
    res = Pipeline(ocr_engine=FakeOcr({0: UNKNOWN_TEXT})).process_file(pdf)
    assert res.extraction.text_source is TextSource.OCR and res.extraction.status is ExtractionStatus.OK
    assert res.doc_class == "unknown" and res.fields is None


def test_mixed_document_ocrs_only_scanned_pages(tmp_path):
    import pymupdf
    text_pdf = pymupdf.open(make_pdf(tmp_path / "t.pdf", [ODWOLANIE_TEXT]))
    scan_pdf = pymupdf.open(make_scan_pdf(tmp_path / "s.pdf", ["załącznik"]))
    text_pdf.insert_pdf(scan_pdf)
    mixed = tmp_path / "mixed.pdf"
    text_pdf.save(mixed)
    ocr = FakeOcr({1: "Załącznik: kopia dowodu nadania odwołania " + "z" * 40})
    res = extract_text(mixed, ocr)
    assert ocr.calls == [1] and res.ocr.reason == "scanned_pages"
    assert res.page_sources == ["native", "ocr"] and res.text_source is TextSource.MIXED


def test_cli_run_with_ocr_diagnostics(tmp_path, monkeypatch):
    import odwolania2json.cli as cli
    monkeypatch.setattr(cli, "engine_from_settings", lambda settings: FakeOcr({0: PRZELEW_TEXT}))
    make_scan_pdf(tmp_path / "in" / "case1" / "dowod.pdf", ["x"])
    out, diag = tmp_path / "output", tmp_path / "diag"
    assert main(["run", str(tmp_path / "in"), "--output", str(out), "--diagnostics", str(diag)]) == 0
    data = json.loads((out / "case1" / "dowod.json").read_text(encoding="utf-8"))
    assert data["przelew_bankowy"]["kwota"] == 7500.0
    d = json.loads((diag / "case1" / "dowod.diag.json").read_text(encoding="utf-8"))
    assert d["extraction"]["text_source"] == "ocr" and d["extraction"]["native_status"] == "no_text"
    assert d["extraction"]["ocr"]["attempted"] and d["extraction"]["ocr"]["reason"] == "no_text"
    summary = (diag / "summary.csv").read_text(encoding="utf-8")
    assert "text_source" in summary.splitlines()[0] and ",ocr," in summary
    assert not list(out.rglob("*.diag.json"))


def test_cli_no_ocr_flag(tmp_path):
    make_scan_pdf(tmp_path / "in" / "s.pdf", ["x"])
    out = tmp_path / "output"
    assert main(["run", str(tmp_path / "in"), "--output", str(out), "--diagnostics", str(tmp_path / "d"),
                 "--no-ocr"]) == 0
    assert json.loads((out / "s.json").read_text(encoding="utf-8")) == {}


# konfiguracja ---------------------------------------------------------------------------------

def test_ocr_settings_and_disabled_engine(tmp_path):
    cfg = tmp_path / "app.toml"
    cfg.write_text('[ocr]\nenabled = false\nlanguage = "pol"\ndpi = 200\n', encoding="utf-8")
    s = load_ocr_settings(cfg)
    assert (s.enabled, s.dpi) == (False, 200) and engine_from_settings(s) is None
    cfg.write_text('[ocr]\ndpi = 2000\n', encoding="utf-8")
    with pytest.raises(ValueError):
        load_ocr_settings(cfg)


def test_find_tessdata_uses_configured_dir(tmp_path):
    (tmp_path / "xyz.traineddata").write_bytes(b"")
    assert find_tessdata("xyz", str(tmp_path)) == tmp_path
    assert find_tessdata("jezyk_ktorego_nie_ma", str(tmp_path)) is None
    engine = PyMuPdfTesseract("jezyk_ktorego_nie_ma", 300, str(tmp_path))
    ok, detail = engine.is_available()
    assert not ok and "jezyk_ktorego_nie_ma.traineddata" in detail


# test integracyjny z rzeczywistym OCR (wymaga pol.traineddata) ---------------------------------

REAL = PyMuPdfTesseract(**{k: v for k, v in vars(OcrSettings()).items() if k in ("language", "dpi")})
real_ocr = pytest.mark.skipif(not REAL.is_available()[0], reason="brak pol.traineddata — test rzeczywistego OCR pominięty")


@real_ocr
def test_real_ocr_reads_polish_scan(tmp_path):
    pdf = make_scan_pdf(tmp_path / "pel.pdf", [PELNOMOCNICTWO_TEXT], dpi=200)
    res = Pipeline(ocr_engine=REAL).process_file(pdf)
    assert res.extraction.text_source is TextSource.OCR
    assert "pełnomocnictwa" in res.text.lower()
    assert res.doc_class == "pelnomocnictwo"
    assert res.fields.data["mocodawca"][0]["data"]["nip"] == "1234567890"


ORGANIZER_SCAN = Path(__file__).parent / "fixtures" / "official" / "szablon_skan_odwolanie.pdf"


@real_ocr
@pytest.mark.skipif(not ORGANIZER_SCAN.exists(), reason="brak skanu z szablonu organizatora w fixtures")
def test_real_ocr_on_organizer_scanned_appeal():
    res = Pipeline(ocr_engine=REAL).process_file(ORGANIZER_SCAN)
    assert res.extraction.text_source is TextSource.OCR
    assert res.doc_class == "odwolanie"
    data = res.fields.data
    assert data["OdwolanieKIO"]["data"] == "2025-06-16" and data["OdwolanieKIO"]["miejscowosc"] == "Warszawa"
    assert data["ZamowieniePubliczne"]["numer_bzp"] == "2025/BZP00270698"
    assert data["ZamowieniePubliczne"]["numer_postepowania"] == "RZP.27.29.2025.KD"
    # Nazwy stron są zaczernione / rozbite przez układ kolumn — oczekujemy null, a nie fragmentów tekstu.
    assert data["Zamawiajacy"][0]["nazwa"] is None
