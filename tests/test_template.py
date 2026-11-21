import json

import pytest

from odwolania2json.extract import build_output, load_template, validate
from odwolania2json.extract.template import TemplateError


@pytest.mark.parametrize("name", ["odwolanie", "pelnomocnictwo", "przelew"])
def test_templates_have_organizer_keys(name):
    """Klucze szablonów = klucze z oficjalnych przykładów (fikstury w tests/fixtures/official_gold)."""
    from .conftest import FIXTURES

    def keys(o, p=""):
        if isinstance(o, dict):
            for k, v in o.items():
                yield f"{p}.{k}"
                yield from keys(v, f"{p}.{k}")
        elif isinstance(o, list):
            for v in o:
                yield from keys(v, p + "[]")

    gold_file = {"odwolanie": "p01_odwolanie.json", "pelnomocnictwo": "p01_pelnomocnictwo.json",
                 "przelew": "p01_potwierdzenie_przelewu.json"}[name]
    gold = json.loads((FIXTURES / "official_gold" / gold_file).read_text(encoding="utf-8"))
    assert set(keys(load_template(name))) == set(keys(gold))


def test_build_output_fills_nulls_and_keeps_order():
    tpl = load_template("przelew")
    out = build_output(tpl, {"przelew_bankowy": {"kwota": "12.5", "nadmiarowe": 1}})
    assert list(out["przelew_bankowy"]) == ["data_operacji", "kwota", "na_rachunek", "numer_transakcji", "tytul", "z_rachunku"]
    assert out["przelew_bankowy"]["kwota"] == 12.5
    assert out["przelew_bankowy"]["data_operacji"] is None
    assert "nadmiarowe" not in out["przelew_bankowy"]
    assert validate(out, tpl) == []


def test_build_output_empty_list_gets_one_null_item():
    out = build_output(load_template("pelnomocnictwo"), {})
    assert out["mocodawca"] == [{"data": {"imie_nazwisko": None, "krs": None, "nazwa": None, "nip": None, "typ": None},
                                 "podpisujacy": [{"imie_nazwisko": None}]}]


def test_build_output_rejects_bad_date_and_int():
    out = build_output(load_template("odwolanie"), {"OdwolanieKIO": {"data": "3.09.2026", "czynnosc_powod_id": "x"}})
    assert out["OdwolanieKIO"]["data"] is None
    assert out["OdwolanieKIO"]["czynnosc_powod_id"] is None


def test_validate_reports_type_missing_and_extra_keys():
    tpl = load_template("przelew")
    bad = {"przelew_bankowy": {"data_operacji": "2026-13", "kwota": "10", "tytul": 5, "x": 1}}
    errors = validate(bad, tpl)
    assert any("data_operacji" in e for e in errors)
    assert any("kwota" in e for e in errors)
    assert any("tytul" in e for e in errors)
    assert any("nadmiarowy klucz" in e for e in errors)
    assert any("brak klucza" in e for e in errors)


def test_pesel_type_is_configurable_via_template(tmp_path):
    tpl = {"pelnomocnicy": [{"imie_nazwisko": "string|null", "pesel": "int|null"}]}
    (tmp_path / "pelnomocnictwo.json").write_text(json.dumps(tpl), encoding="utf-8")
    loaded = load_template("pelnomocnictwo", tmp_path)
    out = build_output(loaded, {"pelnomocnicy": [{"imie_nazwisko": "A B", "pesel": "85071204278"}]})
    assert out["pelnomocnicy"][0]["pesel"] == 85071204278


def test_invalid_template_is_rejected(tmp_path):
    (tmp_path / "x.json").write_text('{"a": "datetime"}', encoding="utf-8")
    with pytest.raises(TemplateError):
        load_template("x", tmp_path)
