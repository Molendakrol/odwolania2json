"""Testy evaluatora (deterministyczne, bez OCR i bez zewnętrznych API)."""

import json
from pathlib import Path

import pytest

from odwolania2json import benchmark
from odwolania2json.cli import main
from odwolania2json.evaluation import ABSENT, compare, diff
from odwolania2json.pipeline import Pipeline

from .conftest import OFFICIAL, FIXTURES, ODWOLANIE_TEXT, UNKNOWN_TEXT, make_pdf


def statuses(pred, gold):
    return {p: s for p, s, _, _ in diff(pred, gold)}


# --- porównanie JSON ----------------------------------------------------------------------------

def test_identical_json_is_all_correct():
    gold = {"a": {"b": "x", "c": None}, "l": [{"k": 1}, {"k": 2}]}
    c = compare(json.loads(json.dumps(gold)), gold)
    assert (c.total.correct, c.total.wrong, c.total.missing, c.total.extra) == (4, 0, 0, 0)
    assert c.total.accuracy == 1.0 and c.total.f1 == 1.0


def test_wrong_values_are_errors_not_normalized_away():
    s = statuses({"a": "Kraków", "n": "200", "d": "2026-01-02", "b": True},
                 {"a": "Krakow", "n": 200, "d": "2026-01-03", "b": 1})
    assert s == {"a": "wrong", "n": "wrong", "d": "wrong", "b": "wrong"}   # diakrytyki, typ, data, bool≠liczba


def test_safe_normalization_only():
    s = statuses({"t": "  Ala   ma\nkota ", "x": 200}, {"t": "Ala ma kota", "x": 200.0})
    assert s == {"t": "correct", "x": "correct"}


def test_null_handling():
    c = compare({"a": None, "b": None, "c": "v"}, {"a": None, "b": "x", "c": None})
    s = {p: st for _, p, st, _, _ in c.details}
    assert s == {"b": "wrong", "c": "wrong"}
    assert c.total.correct == 1                           # null == null
    assert (c.total.tp, c.total.fp, c.total.fn) == (0, 1, 1)


def test_missing_key_is_not_equivalent_to_null():
    s = statuses({"a": 1}, {"a": 1, "b": None})
    assert s["b"] == "missing"
    c = compare({"a": 1}, {"a": 1, "b": None})
    assert c.total.missing == 1 and c.total.accuracy == 0.5 and c.total.fn == 0


def test_extra_key_reported_separately():
    c = compare({"a": 1, "zbedne": "x", "obj": {"q": None}}, {"a": 1})
    assert c.total.extra == 2 and c.total.correct == 1 and c.total.accuracy == 1.0
    assert c.total.fp == 1                                # nadmiarowa wartość nie-null


def test_list_length_differences():
    gold = {"l": [{"n": "A"}, {"n": "B"}]}
    assert statuses({"l": [{"n": "A"}]}, gold) == {"l[0].n": "correct", "l[1].n": "missing"}
    assert statuses({"l": [{"n": "A"}, {"n": "B"}, {"n": "C"}]}, gold)["l[2].n"] == "extra"


def test_structure_mismatch_is_wrong():
    s = statuses({"a": "tekst", "l": {"x": 1}}, {"a": {"b": 1, "c": 2}, "l": [1]})
    assert s == {"a.b": "wrong", "a.c": "wrong", "l[0]": "wrong"}


def test_no_prediction_and_empty_object():
    gold = {"a": {"b": 1}, "c": None}
    assert set(statuses(ABSENT, gold).values()) == {"missing"}
    assert set(statuses({}, gold).values()) == {"missing"}
    assert compare(None, gold).total.missing == 2


# --- metryki klasyfikacji -----------------------------------------------------------------------

def test_classification_metrics():
    pairs = [("odwolanie", "odwolanie"), ("odwolanie", "unknown"), ("pelnomocnictwo", "pelnomocnictwo"),
             ("przelew", "pelnomocnictwo"), ("unknown", "unknown"), ("unknown", "przelew")]
    m = benchmark.classification_metrics(pairs)
    assert m["documents"] == 6 and m["accuracy"] == 0.5
    assert m["confusion_matrix"]["przelew"]["pelnomocnictwo"] == 1
    assert m["per_class"]["odwolanie"] == {"precision": 1.0, "recall": 0.5, "f1": 0.6667, "support": 2, "predicted": 1}
    assert m["per_class"]["przelew"]["f1"] == 0.0
    assert m["false_positives_from_unknown"] == 1 and m["false_unknown"] == 1 and m["cross_class_errors"] == 1
    expected_macro = round((0.6667 + 0.6667 + 0.0 + 0.5) / 4, 4)
    assert abs(m["macro_f1"] - expected_macro) < 1e-3


def test_classification_all_correct():
    m = benchmark.classification_metrics([("unknown", "unknown"), ("przelew", "przelew")])
    assert m["accuracy"] == 1.0 and m["macro_f1"] == 1.0   # klasy bez wsparcia i predykcji pomijane w macro


# --- manifest -----------------------------------------------------------------------------------

def test_manifest_validation(tmp_path):
    good = tmp_path / "m.jsonl"
    good.write_text('# komentarz\n{"pdf": "a.pdf", "label": "przelew", "gt": "a.json"}\n\n', encoding="utf-8")
    [e] = benchmark.load_manifest(good)
    assert e.pdf == tmp_path / "a.pdf" and e.gt == tmp_path / "a.json" and e.group is None
    for bad in ('{"pdf": "a.pdf"}', '{"pdf": "a.pdf", "label": "faktura"}', "nie json",
                '{"pdf": "a.pdf", "label": "unknown"}\n{"pdf": "a.pdf", "label": "unknown"}', ""):
        f = tmp_path / "bad.jsonl"
        f.write_text(bad, encoding="utf-8")
        with pytest.raises(benchmark.ManifestError):
            benchmark.load_manifest(f)


def test_fixtures_manifest_is_valid():
    entries = benchmark.load_manifest(Path(__file__).parents[1] / "datasets" / "manifests" / "fixtures.jsonl")
    assert all(e.pdf.exists() and (e.gt is None or e.gt.exists()) for e in entries)
    assert benchmark.label_counts(entries) == {"odwolanie": 3, "pelnomocnictwo": 1, "przelew": 1, "unknown": 3}


# --- pełny run evaluatora (bez OCR) -------------------------------------------------------------

@pytest.fixture
def mini_manifest(tmp_path):
    """Dokumenty: poprawne, z błędnym GT, błędnie sklasyfikowany, unknown poprawny, skan (bez OCR -> unknown)."""
    gold_ok = (FIXTURES / "official_gold" / "p01_potwierdzenie_przelewu.json").read_text(encoding="utf-8")
    bad_gold = json.loads(gold_ok)
    bad_gold["przelew_bankowy"]["kwota"] = 999.0
    (tmp_path / "bad_gold.json").write_text(json.dumps(bad_gold), encoding="utf-8")
    make_pdf(tmp_path / "pismo.pdf", [UNKNOWN_TEXT])
    make_pdf(tmp_path / "odw.pdf", [ODWOLANIE_TEXT])
    rows = [
        {"pdf": str(OFFICIAL / "p01_potwierdzenie_przelewu.pdf"), "label": "przelew",
         "gt": str(FIXTURES / "official_gold" / "p01_potwierdzenie_przelewu.json"), "group": "g1"},
        {"pdf": str(OFFICIAL / "p01_pelnomocnictwo.pdf"), "label": "pelnomocnictwo",
         "gt": str(FIXTURES / "official_gold" / "p01_pelnomocnictwo.json")},
        {"pdf": str(OFFICIAL / "p01_odwolanie.pdf"), "label": "przelew", "gt": str(tmp_path / "bad_gold.json")},
        {"pdf": "pismo.pdf", "label": "unknown"},
        {"pdf": str(OFFICIAL / "neg_skan_bez_tekstu.pdf"), "label": "odwolanie"},
    ]
    m = tmp_path / "m.jsonl"
    m.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return m


def test_run_evaluation_end_to_end(mini_manifest):
    report = benchmark.run_evaluation(benchmark.load_manifest(mini_manifest), Pipeline(ocr_engine=None), "test")
    c = report["classification"]
    assert c["documents"] == 5 and c["accuracy"] == 0.6
    assert c["confusion_matrix"]["przelew"]["odwolanie"] == 1      # odwołanie oznaczone (celowo) jako przelew
    assert c["confusion_matrix"]["odwolanie"]["unknown"] == 1      # skan bez OCR
    x = report["extraction"]
    assert x["documents_with_ground_truth"] == 3 and x["documents_fully_correct"] == 2
    docs = {d["doc_id"]: d for d in report["documents"]}
    wrong_cls = docs[str(OFFICIAL / "p01_odwolanie.pdf")]
    assert wrong_cls["fields"]["missing"] == 6 and wrong_cls["fields"]["extra"] > 0   # inny szablon
    assert docs["pismo.pdf"]["fields"] is None and docs["pismo.pdf"]["class_correct"]
    assert x["per_type"]["pelnomocnictwo"]["accuracy"] == 1.0
    assert x["worst_documents"][0]["doc_id"] == str(OFFICIAL / "p01_odwolanie.pdf")
    assert "METRYKI WEWNĘTRZNE" in benchmark.format_summary(report)


def test_run_detects_wrong_value(tmp_path):
    gold = json.loads((FIXTURES / "official_gold" / "p01_potwierdzenie_przelewu.json").read_text(encoding="utf-8"))
    gold["przelew_bankowy"]["kwota"] = 999.0
    (tmp_path / "g.json").write_text(json.dumps(gold), encoding="utf-8")
    m = tmp_path / "m.jsonl"
    m.write_text(json.dumps({"pdf": str(OFFICIAL / "p01_potwierdzenie_przelewu.pdf"), "label": "przelew",
                             "gt": "g.json"}) + "\n", encoding="utf-8")
    report = benchmark.run_evaluation(benchmark.load_manifest(m), Pipeline(ocr_engine=None))
    [doc] = report["documents"]
    assert doc["mismatches"] == [{"path": "przelew_bankowy.kwota", "status": "wrong", "pred": 200.0, "gold": 999.0}]
    assert report["extraction"]["worst_fields"][0]["field"] == "przelew_bankowy.kwota"


# --- porównanie dwóch raportów ------------------------------------------------------------------

def _report(docs, cls_acc=1.0, x_acc=1.0, per_field=None, label=""):
    return {"report_version": 1, "label": label, "created_at": "t", "config_fingerprint": label,
            "classification": {"accuracy": cls_acc, "macro_f1": cls_acc},
            "extraction": {"overall": {"accuracy": x_acc, "f1": x_acc, "correct": 0, "wrong": 0, "missing": 0, "extra": 0},
                           "per_field": per_field or {}},
            "documents": docs}


def _doc(doc_id, ok=True, mismatches=()):
    return {"doc_id": doc_id, "class_correct": ok, "fields": {}, "mismatches": [
        {"path": p, "status": "wrong", "pred": 1, "gold": 2} for p in mismatches]}


def test_compare_reports_finds_improvements_and_regressions():
    base = _report([_doc("a", ok=False), _doc("b", mismatches=["x.y"]), _doc("c"), _doc("only_base")],
                   cls_acc=0.75, x_acc=0.9, per_field={"x.y": {"accuracy": 0.5}, "z": {"accuracy": 1.0}}, label="v1")
    new = _report([_doc("a"), _doc("b"), _doc("c", ok=False, mismatches=["z"]), _doc("only_new")],
                  cls_acc=0.75, x_acc=0.95, per_field={"x.y": {"accuracy": 1.0}, "z": {"accuracy": 0.5}}, label="v2")
    cmp = benchmark.compare_reports(base, new)
    assert cmp["common_documents"] == 3
    assert cmp["only_in_base"] == ["only_base"] and cmp["only_in_new"] == ["only_new"]
    assert cmp["classification_fixed"] == ["a"] and cmp["classification_regressions"] == ["c"]
    assert [f["path"] for f in cmp["fields_fixed"]] == ["x.y"]
    assert [(f["doc_id"], f["path"]) for f in cmp["fields_regressions"]] == [("c", "z")]
    assert cmp["extraction"]["accuracy"]["delta"] == 0.05
    assert cmp["per_field_accuracy_changes"]["z"]["delta"] == -0.5
    assert cmp["has_regressions"] is True
    assert "SĄ REGRESJE" in benchmark.format_comparison(cmp)


def test_compare_identical_reports_has_no_regressions():
    r = _report([_doc("a"), _doc("b", mismatches=["x"])])
    cmp = benchmark.compare_reports(r, r)
    assert not cmp["has_regressions"] and cmp["fields_fixed"] == [] and cmp["per_field_accuracy_changes"] == {}


# --- CLI ----------------------------------------------------------------------------------------

def test_cli_eval_and_compare(mini_manifest, tmp_path, capsys):
    r1, r2 = tmp_path / "r1.json", tmp_path / "r2.json"
    assert main(["eval", "--manifest", str(mini_manifest), "--report", str(r1), "--no-ocr", "--label", "a"]) == 0
    assert main(["eval", "--manifest", str(mini_manifest), "--report", str(r2), "--no-ocr", "--label", "b"]) == 0
    out = capsys.readouterr().out
    assert "KLASYFIKACJA" in out and "EKSTRAKCJA" in out and "macierz pomyłek" in out
    assert json.loads(r1.read_text(encoding="utf-8"))["classification"]["accuracy"] == 0.6
    assert main(["eval-compare", str(r1), str(r2), "--fail-on-regression", "--report", str(tmp_path / "c.json")]) == 0
    assert "brak regresji" in capsys.readouterr().out
    # sztuczna regresja w drugim raporcie
    rep = json.loads(r2.read_text(encoding="utf-8"))
    rep["documents"][0]["mismatches"].append({"path": "przelew_bankowy.tytul", "status": "wrong", "pred": "a", "gold": "b"})
    r2.write_text(json.dumps(rep), encoding="utf-8")
    assert main(["eval-compare", str(r1), str(r2), "--fail-on-regression"]) == 1


def test_cli_eval_missing_files(tmp_path):
    m = tmp_path / "m.jsonl"
    m.write_text('{"pdf": "brak.pdf", "label": "unknown"}\n', encoding="utf-8")
    assert main(["eval", "--manifest", str(m), "--report", str(tmp_path / "r.json"), "--no-ocr"]) == 2


def test_cli_manifest_organizer(tmp_path):
    dane = tmp_path / "dane"
    (dane / "przyklad_01").mkdir(parents=True)
    (dane / "negatywne").mkdir()
    for name in ("odwolanie.pdf", "potwierdzenie_przelewu.pdf", "ogloszenie.pdf"):
        (dane / "przyklad_01" / name).write_bytes(b"%PDF")
    (dane / "przyklad_01" / "odwolanie.json").write_text("{}")
    (dane / "przyklad_01" / "przelew.json").write_text("{}")
    (dane / "negatywne" / "x.pdf").write_bytes(b"%PDF")
    out = tmp_path / "m.jsonl"
    assert main(["manifest-organizer", "--dane", str(dane), "--out", str(out)]) == 0
    rows = {Path(r["pdf"]).name: r for r in map(json.loads, out.read_text().splitlines())}
    assert rows["odwolanie.pdf"]["label"] == "odwolanie" and rows["odwolanie.pdf"]["gt"].endswith("odwolanie.json")
    assert rows["potwierdzenie_przelewu.pdf"]["gt"].endswith("przelew.json")
    assert rows["ogloszenie.pdf"]["label"] == "unknown" and rows["ogloszenie.pdf"]["gt"] is None
    assert rows["x.pdf"]["label"] == "unknown" and rows["x.pdf"]["group"] == "negatywne"
