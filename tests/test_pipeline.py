"""Testy end-to-end: PDF -> pipeline -> wynik, oraz CLI.

Dokumenty organizatora w tests/fixtures/official to test regresyjny (znane szablony),
a nie dowód generalizacji.
"""

import json
import os
import shutil
from pathlib import Path

import pytest

from odwolania2json.cli import main
from odwolania2json.pipeline import Pipeline, ProcessingStatus

from .conftest import OFFICIAL, ODWOLANIE_TEXT, PELNOMOCNICTWO_TEXT, PRZELEW_TEXT, UNKNOWN_TEXT, make_pdf

EXPECTED_OFFICIAL = {
    "p01_odwolanie.pdf": "odwolanie",
    "p42_odwolanie.pdf": "odwolanie",
    "p01_pelnomocnictwo.pdf": "pelnomocnictwo",
    "p01_potwierdzenie_przelewu.pdf": "przelew",
    "neg_postanowienie_kio.pdf": "unknown",
    "neg_ogloszenie_bzp.pdf": "unknown",
    "neg_skan_bez_tekstu.pdf": "unknown",
}


@pytest.fixture(scope="module")
def pipeline():
    # OCR wyłączony: testy nie zależą od obecności danych Tesseracta (OCR testowany w test_ocr.py).
    return Pipeline(ocr_engine=None)


@pytest.mark.parametrize(("name", "expected"), sorted(EXPECTED_OFFICIAL.items()))
def test_official_fixtures_regression(pipeline, name, expected):
    res = pipeline.process_file(OFFICIAL / name)
    assert res.doc_class == expected, res.classification.reason


def test_scan_without_text_reports_no_text_status(pipeline):
    res = pipeline.process_file(OFFICIAL / "neg_skan_bez_tekstu.pdf")
    assert res.status is ProcessingStatus.NO_TEXT
    assert res.classification.reason == "no_text"


@pytest.fixture
def case_dir(tmp_path):
    """Struktura jak w konkursie: input/<sprawa>/<plik>.pdf; nazwy plików celowo mylące."""
    root = tmp_path / "input"
    make_pdf(root / "case1" / "dokument_1.pdf", [ODWOLANIE_TEXT])
    make_pdf(root / "case1" / "skan_002.PDF", [PELNOMOCNICTWO_TEXT])
    make_pdf(root / "case1" / "odwolanie.pdf", [PRZELEW_TEXT])          # nazwa sugeruje inną klasę
    make_pdf(root / "case2" / "zalacznik.pdf", [UNKNOWN_TEXT])
    (root / "case2" / "uszkodzony.pdf").write_bytes(b"%PDF-1.7 uszkodzony")
    (root / "case2" / "notatka.txt").write_text("nie PDF")
    return root


def test_directory_end_to_end(pipeline, case_dir):
    results = {r.relative_path: r for r in pipeline.process_dir(case_dir)}
    assert set(results) == {
        "case1/dokument_1.pdf", "case1/odwolanie.pdf", "case1/skan_002.PDF",
        "case2/uszkodzony.pdf", "case2/zalacznik.pdf",
    }
    assert results["case1/dokument_1.pdf"].doc_class == "odwolanie"
    assert results["case1/skan_002.PDF"].doc_class == "pelnomocnictwo"
    assert results["case1/odwolanie.pdf"].doc_class == "przelew"   # klasyfikacja po treści, nie nazwie
    assert results["case2/zalacznik.pdf"].doc_class == "unknown"
    broken = results["case2/uszkodzony.pdf"]
    assert broken.status is ProcessingStatus.ERROR and broken.doc_class == "unknown"
    assert broken.extraction.error


def test_result_contains_required_fields(pipeline, case_dir):
    res = pipeline.process_file(case_dir / "case1" / "dokument_1.pdf", case_dir)
    data = res.to_dict(include_text=True)
    assert data["file_name"] == "dokument_1.pdf"
    assert data["relative_path"] == "case1/dokument_1.pdf"
    assert data["status"] == "ok"
    assert data["doc_class"] == "odwolanie"
    assert data["extraction"]["page_count"] == 1
    assert data["classification"]["signals"]["odwolanie"]
    assert "Prezesa Krajowej Izby" in data["text"]


def test_cli_directory_writes_diagnostics(case_dir, tmp_path, capsys):
    out = tmp_path / "diag"
    assert main(["classify", str(case_dir), "--output", str(out), "--save-text"]) == 0
    printed = capsys.readouterr().out
    assert "case1/dokument_1.pdf" in printed and "odwolanie" in printed
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert summary["documents"] == 5
    assert summary["by_class"] == {"odwolanie": 1, "pelnomocnictwo": 1, "przelew": 1, "unknown": 2}
    assert (out / "case1" / "dokument_1.diag.json").exists()
    assert (out / "case1" / "dokument_1.txt").exists()
    assert (out / "summary.csv").read_text(encoding="utf-8").startswith("relative_path,")


def test_cli_single_file_json_output(tmp_path, capsys):
    pdf = make_pdf(tmp_path / "x.pdf", [PRZELEW_TEXT])
    assert main(["classify", str(pdf), "--output", str(tmp_path / "d"), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data[0]["doc_class"] == "przelew"


def test_cli_uses_input_option_when_no_path(case_dir, tmp_path):
    assert main(["classify", "--input", str(case_dir), "--output", str(tmp_path / "d")]) == 0


def test_cli_missing_path_returns_error_code(tmp_path):
    assert main(["classify", str(tmp_path / "nie_ma"), "--output", str(tmp_path / "d")]) == 2


def test_cli_empty_directory_returns_nonzero(tmp_path):
    (tmp_path / "pusty").mkdir()
    assert main(["classify", str(tmp_path / "pusty"), "--output", str(tmp_path / "d")]) == 1


def test_cli_invalid_rules_file_returns_error_code(tmp_path):
    rules = tmp_path / "rules.toml"
    rules.write_text("[zones]\nhead_chars = 10\n", encoding="utf-8")
    pdf = make_pdf(tmp_path / "x.pdf", [PRZELEW_TEXT])
    assert main(["classify", str(pdf), "--rules", str(rules), "--output", str(tmp_path / "d")]) == 2


# --- CLI run: zapis JSON-ów w formacie organizatora ---------------------------------------------

def test_cli_run_writes_organizer_layout(case_dir, tmp_path, capsys):
    out, diag = tmp_path / "output", tmp_path / "diag"
    assert main(["run", str(case_dir), "--output", str(out), "--diagnostics", str(diag)]) == 0
    files = sorted(p.relative_to(out).as_posix() for p in out.rglob("*.json"))
    assert files == ["case1/dokument_1.json", "case1/odwolanie.json", "case1/skan_002.json",
                     "case2/uszkodzony.json", "case2/zalacznik.json"]
    odw = json.loads((out / "case1" / "dokument_1.json").read_text(encoding="utf-8"))
    assert set(odw) == {"OdwolanieKIO", "Odwolujacy", "Zamawiajacy", "ZamowieniePubliczne"}
    prz = json.loads((out / "case1" / "odwolanie.json").read_text(encoding="utf-8"))   # treść = przelew
    assert set(prz) == {"przelew_bankowy"}
    # unknown (oraz nieczytelny PDF) -> poprawny, pusty obiekt JSON zakończony znakiem nowej linii
    for name in ("zalacznik.json", "uszkodzony.json"):
        raw = (out / "case2" / name).read_text(encoding="utf-8")
        assert raw == "{}\n"
        assert json.loads(raw) == {}
    diag_doc = json.loads((diag / "case1" / "dokument_1.diag.json").read_text(encoding="utf-8"))
    assert diag_doc["fields"]["validation_errors"] == []
    assert diag_doc["fields"]["evidence"]
    assert not list(out.rglob("*.diag.json")) and not (out / "summary.json").exists()   # diagnostyka poza output


def test_unknown_document_is_not_passed_to_extractor(pipeline, case_dir):
    res = pipeline.process_file(case_dir / "case2" / "zalacznik.pdf", case_dir)
    assert res.doc_class == "unknown"
    assert res.fields is None


def test_cli_run_empty_file_mode_still_available(case_dir, tmp_path):
    cfg = tmp_path / "app.toml"
    cfg.write_text('[output]\nunknown = "empty_file"\n', encoding="utf-8")
    out = tmp_path / "output"
    assert main(["run", str(case_dir), "--output", str(out), "--diagnostics", str(tmp_path / "d"),
                 "--app-config", str(cfg)]) == 0
    assert (out / "case2" / "zalacznik.json").stat().st_size == 0


def test_cli_run_refuses_to_overwrite_without_flag(case_dir, tmp_path):
    out, diag = tmp_path / "output", tmp_path / "d"
    assert main(["run", str(case_dir), "--output", str(out), "--diagnostics", str(diag)]) == 0
    marker = out / "case1" / "dokument_1.json"
    marker.write_text('{"zmienione": true}\n', encoding="utf-8")
    assert main(["run", str(case_dir), "--output", str(out), "--diagnostics", str(diag)]) == 3
    assert json.loads(marker.read_text(encoding="utf-8")) == {"zmienione": True}       # nic nie nadpisano
    assert main(["run", str(case_dir), "--output", str(out), "--diagnostics", str(diag), "--overwrite"]) == 0
    assert "OdwolanieKIO" in json.loads(marker.read_text(encoding="utf-8"))


def test_cli_run_rejects_output_inside_input_and_diagnostics_inside_output(case_dir, tmp_path):
    assert main(["run", str(case_dir), "--output", str(case_dir / "out"), "--diagnostics", str(tmp_path / "d")]) == 2
    out = tmp_path / "output"
    assert main(["run", str(case_dir), "--output", str(out), "--diagnostics", str(out / "diag")]) == 2
    assert not out.exists()
    assert not (case_dir / "out").exists()


def test_invalid_result_is_not_written(pipeline, case_dir, tmp_path):
    from odwolania2json.output import write_outputs

    res = pipeline.process_file(case_dir / "case1" / "dokument_1.pdf", case_dir)
    res.fields.validation_errors = ["$.OdwolanieKIO.data: oczekiwano daty"]
    report = write_outputs([res], tmp_path / "output")
    assert report.written == []
    assert report.rejected == [("case1/dokument_1.pdf", ["$.OdwolanieKIO.data: oczekiwano daty"])]
    assert not (tmp_path / "output" / "case1" / "dokument_1.json").exists()


def test_cli_run_single_pdf(tmp_path):
    pdf = make_pdf(tmp_path / "in" / "dowod.pdf", [PRZELEW_TEXT])
    out = tmp_path / "output"
    assert main(["run", str(pdf), "--output", str(out), "--diagnostics", str(tmp_path / "d")]) == 0
    data = json.loads((out / "dowod.json").read_text(encoding="utf-8"))
    assert data["przelew_bankowy"]["kwota"] == 7500.0


def test_cli_run_invalid_app_config(case_dir, tmp_path):
    cfg = tmp_path / "app.toml"
    cfg.write_text('[output]\nunknown = "null"\n', encoding="utf-8")
    assert main(["run", str(case_dir), "--output", str(tmp_path / "o"), "--app-config", str(cfg)]) == 2


def test_cli_evaluate(tmp_path, capsys):
    gold, pred = tmp_path / "gold" / "c", tmp_path / "pred" / "c"
    gold.mkdir(parents=True)
    pred.mkdir(parents=True)
    (gold / "odwolanie.json").write_text('{"a": 1}')
    (pred / "odwolanie.json").write_text('{"a": 1}')
    assert main(["evaluate", "--pred", str(tmp_path / "pred"), "--gold", str(tmp_path / "gold")]) == 0
    assert "METRYKI WEWNĘTRZNE" in capsys.readouterr().out


# --- opcjonalnie: cały zbiór organizatora (ustaw ODWOLANIA_DANE=<repo organizatora>/dane) ------

DANE = os.environ.get("ODWOLANIA_DANE")
GOLD_BY_NAME = {"odwolanie.pdf": "odwolanie", "pelnomocnictwo.pdf": "pelnomocnictwo",
                "potwierdzenie_przelewu.pdf": "przelew", "przelew.pdf": "przelew"}   # organizator zmienił nazwę 21.11


def _official_subset(tmp_path: Path) -> Path:
    """Oficjalny zbiór regresyjny = przyklad_* + negatywne (bez dane/skany — osobne manifesty DEV/HOLDOUT)."""
    root = tmp_path / "official"
    root.mkdir()
    for d in Path(DANE).iterdir():
        if d.is_dir() and d.name != "skany":
            shutil.copytree(d, root / d.name)
    return root


@pytest.mark.skipif(not DANE, reason="ustaw ODWOLANIA_DANE, aby uruchomić regresję na pełnym zbiorze organizatora")
def test_full_official_dataset_regression(pipeline, tmp_path):
    errors = []
    for res in pipeline.process_dir(_official_subset(tmp_path)):
        parts = Path(res.relative_path).parts
        gold = "unknown" if parts[0] == "negatywne" else GOLD_BY_NAME.get(parts[-1], "unknown")
        if res.doc_class != gold:
            errors.append((res.relative_path, gold, res.doc_class, res.classification.reason))
    assert errors == []


@pytest.mark.skipif(not DANE, reason="ustaw ODWOLANIA_DANE, aby uruchomić regresję ekstrakcji na zbiorze organizatora")
def test_full_official_dataset_extraction_regression(tmp_path):
    from odwolania2json.evaluation import evaluate_dirs, format_report

    out = tmp_path / "output"
    official = _official_subset(tmp_path)
    assert main(["run", str(official), "--output", str(out), "--diagnostics", str(tmp_path / "d")]) == 0
    cmp = evaluate_dirs(out, official)
    # Próg regresji (dane znane podczas strojenia reguł) — spadek sygnalizuje regresję, nie mierzy generalizacji.
    assert cmp.total.f1 >= 0.99, format_report(cmp)
