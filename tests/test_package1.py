"""Pakiet 1: przetwarzanie równoległe, prefiks kraju w rachunkach (przełącznik), korekty OCR."""

import json
import tomllib
from functools import partial
from pathlib import Path

from odwolania2json.cli import build_pipeline, main
from odwolania2json.extract import EXTRACTION_CONFIG, FieldExtractor
from odwolania2json.extract.normalizers import (
    account_digits, fix_ampersand, normalize_account, strip_lead_letter,
)
from odwolania2json.parallel import default_workers, process_paths

from .conftest import ODWOLANIE_TEXT, PELNOMOCNICTWO_TEXT, PRZELEW_TEXT, UNKNOWN_TEXT, make_pdf

OFFICIAL = Path(__file__).parent / "fixtures" / "official"


# --- rachunki: prefiks kraju ------------------------------------------------------------------------

def test_account_prefix_default_matches_organizer_examples():
    assert normalize_account("PL61 1090 1014 0000 0712 1981 2874") == "61109010140000071219812874"
    assert normalize_account("61 1090 1014 0000 0712 1981 2874") == "61109010140000071219812874"


def test_account_prefix_kept_only_when_in_document():
    assert normalize_account("PL61 1090 1014 0000 0712 1981 2874", keep_prefix=True) == "PL61109010140000071219812874"
    assert normalize_account("pl61109010140000071219812874", keep_prefix=True) == "PL61109010140000071219812874"
    assert normalize_account("61 1090 1014 0000 0712 1981 2874", keep_prefix=True) == "61109010140000071219812874"
    assert normalize_account("PL61 1090", keep_prefix=True) is None
    assert account_digits("PL61109010140000071219812874") == "61109010140000071219812874" and account_digits(None) is None


def test_default_config_does_not_keep_prefix():
    with open(EXTRACTION_CONFIG, "rb") as fh:
        assert tomllib.load(fh)["przelew"]["keep_country_prefix"] is False


def _extract_przelew(text: str, keep: bool, tmp_path: Path) -> dict:
    with open(EXTRACTION_CONFIG, "rb") as fh:
        raw = fh.read().decode("utf-8")
    cfg = tmp_path / "extraction.toml"
    cfg.write_text(raw.replace("keep_country_prefix = false", f"keep_country_prefix = {str(keep).lower()}"),
                   encoding="utf-8")
    return FieldExtractor(config_path=cfg).extract("przelew", [text]).data["przelew_bankowy"]


def test_przelew_extractor_respects_prefix_switch(tmp_path):
    off = _extract_przelew(PRZELEW_TEXT, False, tmp_path)
    on = _extract_przelew(PRZELEW_TEXT, True, tmp_path)
    assert off["z_rachunku"] == "61109010140000071219812874"
    assert on["z_rachunku"] == "PL61109010140000071219812874"
    assert on["na_rachunek"] == off["na_rachunek"] == "60101010100081362231000000"   # bez PL w dokumencie


def test_unlabeled_account_fill_treats_prefixed_and_plain_as_same(tmp_path):
    # ten sam rachunek raz z PL, raz bez + drugi rachunek bez etykiety: uzupełnienie musi wybrać ten drugi
    text = ("Potwierdzenie przelewu\nZ rachunku: PL61 1090 1014 0000 0712 1981 2874\nKwota: 17,00 PLN\n"
            "Powtórzenie: 61 1090 1014 0000 0712 1981 2874\nOdbiorca\n60 1010 1010 0081 3622 3100 0000\n")
    out = _extract_przelew(text, True, tmp_path)
    assert out["z_rachunku"] == "PL61109010140000071219812874"
    assert out["na_rachunek"] == "60101010100081362231000000"


# --- korekty OCR --------------------------------------------------------------------------------------

def test_fix_ampersand_only_between_words():
    assert fix_ampersand("Kancelaria Prawna Olszewski £ Partnerzy sp. k.") == "Kancelaria Prawna Olszewski & Partnerzy sp. k."
    assert fix_ampersand("cena 10 £") == "cena 10 £"           # na końcu — bez zmian
    assert fix_ampersand("£ 10") == "£ 10"


def test_strip_lead_letter():
    assert strip_lead_letter("i WPIS OD ODWOŁANIA, DA.ZP.242.13.2026") == "WPIS OD ODWOŁANIA, DA.ZP.242.13.2026"
    assert strip_lead_letter("w KIO sygn. akt") == "w KIO sygn. akt"           # przyimek zostaje
    assert strip_lead_letter("z UZP") == "z UZP"
    assert strip_lead_letter("opłata skarbowa") == "opłata skarbowa"
    assert strip_lead_letter("a BC") == "a BC"                                    # za krótkie wersaliki
    assert strip_lead_letter(None) is None


def test_extraction_applies_ocr_fixes():
    ex = FieldExtractor()
    text = PRZELEW_TEXT.replace("Tytuł: wpis od odwołania", "Tytuł: i WPIS OD ODWOŁANIA")
    assert ex.extract("przelew", [text]).data["przelew_bankowy"]["tytul"].startswith("WPIS OD ODWOŁANIA")


# --- przetwarzanie równoległe -------------------------------------------------------------------------

def _case(tmp_path: Path) -> Path:
    case = tmp_path / "in" / "sprawa"
    for name, text in [("a_odwolanie.pdf", ODWOLANIE_TEXT), ("b_pelnomocnictwo.pdf", PELNOMOCNICTWO_TEXT),
                       ("c_przelew.pdf", PRZELEW_TEXT), ("d_pismo.pdf", UNKNOWN_TEXT)]:
        make_pdf(case / name, [text])
    return tmp_path / "in"


def test_default_workers_in_range():
    assert 1 <= default_workers() <= 8


def test_parallel_results_equal_sequential_and_keep_order(tmp_path):
    base = _case(tmp_path)
    paths = sorted(base.rglob("*.pdf"))
    factory = partial(build_pipeline, str(Path(__file__).parents[1] / "config" / "classifier_rules.toml"),
                      str(Path(__file__).parents[1] / "config" / "app.toml"), True, True)
    seq = process_paths(paths, base, factory, workers=1, progress=False)
    par = process_paths(paths, base, factory, workers=3, progress=False)
    assert [r.relative_path for r in par] == [p.relative_to(base).as_posix() for p in paths]
    assert [r.doc_class for r in par] == ["odwolanie", "pelnomocnictwo", "przelew", "unknown"]
    assert [r.fields.data if r.fields else None for r in par] == [r.fields.data if r.fields else None for r in seq]


def test_cli_run_parallel_output_identical_to_sequential(tmp_path):
    base = _case(tmp_path)
    shutil_copy = (OFFICIAL / "p01_potwierdzenie_przelewu.pdf").read_bytes()
    (base / "sprawa" / "e_oficjalny_przelew.pdf").write_bytes(shutil_copy)
    outs = {}
    for w in ("1", "3"):
        out, diag = tmp_path / f"out{w}", tmp_path / f"diag{w}"
        assert main(["run", str(base), "--output", str(out), "--diagnostics", str(diag), "--no-ocr", "--workers", w]) == 0
        outs[w] = {p.relative_to(out).as_posix(): p.read_bytes() for p in sorted(out.rglob("*.json"))}
    assert len(outs["1"]) == 5 and outs["1"] == outs["3"]
    assert json.loads(outs["3"]["sprawa/d_pismo.json"]) == {}


def test_cli_run_rejects_invalid_workers(tmp_path):
    base = _case(tmp_path)
    assert main(["run", str(base), "--output", str(tmp_path / "o"), "--diagnostics", str(tmp_path / "d"),
                 "--workers", "0"]) == 2
