"""Testy ekstraktorów pól na tekstach pisanych od zera (inne układy niż w danych organizatora)
oraz regresja na oficjalnych przykładach. Wynik na danych organizatora nie dowodzi generalizacji."""

import json

import pytest

from odwolania2json.extract import FieldExtractor
from odwolania2json.pdf_text import extract_text

from .conftest import FIXTURES, OFFICIAL, ODWOLANIE_TEXT, PRZELEW_TEXT


@pytest.fixture(scope="module")
def fx():
    return FieldExtractor()


# --- odwołanie -------------------------------------------------------------------------------

ODWOLANIE_KONSORCJUM = """Warszawa, 12 stycznia 2027 r.
Prezes Krajowej Izby Odwoławczej
Odwołujący:
Konsorcjum firm:
Lider Konsorcjum: Alfa Budowa S.A., ul. Długa 5, 00-001 Warszawa, KRS 0000111111, NIP 5250000001, REGON 140000001
Partner Konsorcjum: Beta Instal sp. z o.o., ul. Krótka 2, 00-002 Warszawa, KRS 0000222222, NIP 5250000002
reprezentowany przez: adw. Marta Zielińska, adw. Piotr Lis
adres do korespondencji: Kancelaria Adwokacka Zielińska Lis s.c., ul. Prosta 1, 00-003 Warszawa, e-mail: kancelaria@zl.example
Zamawiający (zamawiający wspólnie):
1) Zamawiający – prowadzący postępowanie: Miasto Testowe, ul. Rynek 1, 00-004 Testowo, NIP 5250000003, REGON 000000003
2) Zamawiający – uczestnik: Spółka Wodna Testowo sp. z o.o., ul. Wodna 2, 00-005 Testowo, KRS 0000333333, NIP 5250000004
dotyczy: postępowania pn. „Przebudowa mostu na rzece Testowej”, znak sprawy: ZP.271.7.2027,
ogłoszenie w Dzienniku Urzędowym UE nr 2027/S 004-001234, ogłoszenie w BZP nr 2027/BZP 00012345/02
ODWOŁANIE
Działając w imieniu Odwołującego wnoszę odwołanie.
Zamawiający w dniu 5 stycznia 2027 r. dokonał wyboru oferty najkorzystniejszej złożonej przez wykonawcę Gamma sp. z o.o.
Jednocześnie w dniu 3 stycznia 2027 r. Zamawiający wezwał Odwołującego do wyjaśnień.
Zarzut nr 1 – zaniechania odrzucenia oferty wykonawcy Gamma sp. z o.o., tj. naruszenie art. 226 ust. 1 pkt 5 Pzp
W uzasadnieniu wskazujemy też postępowanie 2025/S 100-009999 oraz NIP 9999999999 innego wykonawcy."""


def test_odwolanie_simple_layout(fx):
    data = fx.extract("odwolanie", [ODWOLANIE_TEXT]).data
    kio = data["OdwolanieKIO"]
    assert kio["data"] == "2027-02-05" and kio["miejscowosc"] == "Szczecin"
    assert kio["czynnosc_powod_id"] == 1 and kio["czynnosc_opis"] == "Decyzja o odrzuceniu oferty"
    assert kio["czynnosc_dzien"] is None            # w tekście brak daty czynności
    odw = data["Odwolujacy"][0]
    assert (odw["nazwa"], odw["krs"]) == ("Testbud Sp. z o.o.", "0000000001")
    assert data["Zamawiajacy"][0]["nazwa"] == "Gmina Testowo"
    assert data["Zamawiajacy"][0]["prowadzacy_postepowanie"] is None


def test_odwolanie_consortium_joint_contracting_and_distractors(fx):
    res = fx.extract("odwolanie", [ODWOLANIE_KONSORCJUM])
    d = res.data
    assert res.validation_errors == []
    assert d["OdwolanieKIO"]["data"] == "2027-01-12" and d["OdwolanieKIO"]["miejscowosc"] == "Warszawa"
    # zarzut nr 1 decyduje o kategorii; data — ze zdania o czynności Zamawiającego, nie z „wezwał do wyjaśnień”
    assert d["OdwolanieKIO"]["czynnosc_powod_id"] == 7
    assert d["OdwolanieKIO"]["czynnosc_dzien"] == "2027-01-05"
    odw = d["Odwolujacy"][0]
    assert odw["nazwa"] == "Alfa Budowa S.A." and odw["nip"] == "5250000001" and odw["regon"] == "140000001"
    assert odw["przez-pelnomocnika"] == 1
    assert odw["pelnomocnik"] == {"imie_nazwisko": "Marta Zielińska", "nazwa": "Kancelaria Adwokacka Zielińska Lis s.c.",
                                  "mail": "kancelaria@zl.example"}
    zam = d["Zamawiajacy"]
    assert [z["nazwa"] for z in zam] == ["Miasto Testowe", "Spółka Wodna Testowo sp. z o.o."]
    assert [z["prowadzacy_postepowanie"] for z in zam] == [1, 0]
    assert zam[0]["krs"] is None and zam[1]["krs"] == "0000333333"
    zp = d["ZamowieniePubliczne"]
    assert zp == {"numer_bzp": "2027/BZP00012345/02", "numer_postepowania": "ZP.271.7.2027",
                  "numer_ted": "001234-2027", "tytul": "Przebudowa mostu na rzece Testowej"}


def test_odwolanie_without_header_date_and_representative(fx):
    text = ("Do Prezesa Krajowej Izby Odwoławczej\nOdwołujący: Delta sp. j., ul. Polna 1, 00-001 Testowo, NIP 5250000009\n"
            "Zamawiający: Gmina X, ul. Y 1, 00-002 X\nODWOŁANIE\nZamawiający w dniu 2 marca 2027 r. unieważnił postępowanie.")
    d = fx.extract("odwolanie", [text]).data
    assert d["OdwolanieKIO"]["data"] is None and d["OdwolanieKIO"]["miejscowosc"] is None
    assert d["OdwolanieKIO"]["czynnosc_powod_id"] == 9 and d["OdwolanieKIO"]["czynnosc_dzien"] == "2027-03-02"
    odw = d["Odwolujacy"][0]
    assert odw["przez-pelnomocnika"] == 0
    assert odw["pelnomocnik"] == {"imie_nazwisko": None, "nazwa": None, "mail": None}
    assert d["ZamowieniePubliczne"]["numer_ted"] is None and d["ZamowieniePubliczne"]["numer_bzp"] is None


def test_odwolanie_invalid_dates_give_null(fx):
    text = ("Poznań, dnia 31.02.2027 r.\nPrezes Krajowej Izby Odwoławczej\nOdwołujący: Delta sp. j., ul. Polna 1, "
            "00-001 Testowo\nZamawiający: Gmina X, ul. Y 1, 00-002 X\nODWOŁANIE\n"
            "Zamawiający w dniu 45 marca 2027 r. unieważnił postępowanie.")
    kio = fx.extract("odwolanie", [text]).data["OdwolanieKIO"]
    assert kio["data"] is None                       # 31 lutego nie istnieje
    assert kio["miejscowosc"] == "Poznań"            # miejscowość z poprawnie rozpoznanej linii nagłówka
    assert kio["czynnosc_dzien"] is None             # brak poprawnej daty czynności
    assert kio["czynnosc_powod_id"] == 9


def test_odwolanie_malformed_identifiers_give_null(fx):
    text = ("Prezes Krajowej Izby Odwoławczej\nOdwołujący: Delta S.A., ul. Polna 1, 00-001 Testowo, "
            "KRS 12345, NIP 525-000-00-1, REGON 1234567\nZamawiający: Gmina X, ul. Y 1, 00-002 X, NIP 525-000-00-03\n"
            "ODWOŁANIE")
    d = fx.extract("odwolanie", [text]).data
    odw = d["Odwolujacy"][0]
    assert (odw["krs"], odw["nip"], odw["regon"]) == (None, None, None)   # złe długości -> null
    assert d["Zamawiajacy"][0]["nip"] == "5250000003"                       # separatory usunięte


def test_odwolanie_identifiers_of_representative_not_assigned_to_appellant(fx):
    text = ("Prezes Krajowej Izby Odwoławczej\nOdwołujący: Epsilon S.A., ul. Polna 1, 00-001 Testowo\n"
            "reprezentowany przez: r. pr. Jan Lis, Kancelaria Lis sp.k., ul. Nowa 2, 00-002 X, NIP 5250000099\n"
            "Zamawiający: Gmina X, ul. Y 1, 00-002 X\nODWOŁANIE")
    odw = fx.extract("odwolanie", [text]).data["Odwolujacy"][0]
    assert odw["nip"] is None                         # NIP kancelarii to nie NIP Odwołującego
    assert odw["pelnomocnik"]["imie_nazwisko"] == "Jan Lis"


# --- pełnomocnictwo ----------------------------------------------------------------------------

PELNOMOCNICTWO_KONSORCJUM = """Kraków, 10.01.2027
PEŁNOMOCNICTWO
Wykonawcy wspólnie ubiegający się o zamówienie: 1) Alfa Budowa S.A., ul. Długa 5, 00-001 Warszawa,
KRS 0000111111, NIP 5250000001; 2) Beta Instal sp. z o.o., ul. Krótka 2, 00-002 Warszawa, KRS 0000222222,
NIP 5250000002 (dalej: „Mocodawca”), udzielają pełnomocnictwa: adw. Marta Zielińska, PESEL 90010112345,
adw. Piotr Lis do reprezentowania Mocodawcy przed Krajową Izbą Odwoławczą.
W imieniu Alfa Budowa S.A.:
..............................
Jan Nowak
Prezes Zarządu
W imieniu Beta Instal sp. z o.o.:
..............................
Ewa Kowalczyk
Członek Zarządu
Pełnomocnictwo przyjmuję:
..............................
adw. Marta Zielińska"""


def test_pelnomocnictwo_consortium(fx):
    res = fx.extract("pelnomocnictwo", [PELNOMOCNICTWO_KONSORCJUM])
    assert res.validation_errors == []
    m = res.data["mocodawca"]
    assert [x["data"]["nazwa"] for x in m] == ["Alfa Budowa S.A.", "Beta Instal sp. z o.o."]
    assert [x["data"]["krs"] for x in m] == ["0000111111", "0000222222"]
    assert all(x["data"]["typ"] == "osoba_prawna" and x["data"]["imie_nazwisko"] is None for x in m)
    assert m[0]["podpisujacy"] == [{"imie_nazwisko": "Jan Nowak"}]
    assert m[1]["podpisujacy"] == [{"imie_nazwisko": "Ewa Kowalczyk"}]
    assert res.data["pelnomocnicy"] == [{"imie_nazwisko": "Marta Zielińska", "pesel": "90010112345"},
                                        {"imie_nazwisko": "Piotr Lis", "pesel": None}]


def test_pelnomocnictwo_sole_trader(fx):
    text = ("PEŁNOMOCNICTWO\nJa, niżej podpisany Tomasz Testowy, prowadzący działalność gospodarczą pod firmą "
            "TOM-TEST Tomasz Testowy, ul. Leśna 3, 00-010 Testowo, NIP 5250000011, ustanawiam pełnomocnikiem "
            "r. pr. Annę Kowal do reprezentowania mnie w postępowaniu odwoławczym.\n...............\nTomasz Testowy")
    m = fx.extract("pelnomocnictwo", [text]).data["mocodawca"][0]
    assert m["data"] == {"imie_nazwisko": "Tomasz Testowy", "krs": None, "nazwa": "TOM-TEST Tomasz Testowy",
                         "nip": "5250000011", "typ": "osoba_fizyczna"}
    assert m["podpisujacy"] == [{"imie_nazwisko": "Tomasz Testowy"}]


@pytest.mark.xfail(strict=True, reason="Znane ograniczenie: brak odmiany imion do mianownika (np. 'Annę Kowal')")
def test_pelnomocnik_name_in_accusative_is_converted_to_nominative(fx):
    text = ("PEŁNOMOCNICTWO\nAlfa S.A., KRS 0000111111, NIP 5250000001, ustanawia pełnomocnikiem "
            "r. pr. Annę Kowal do reprezentowania Mocodawcy.")
    assert fx.extract("pelnomocnictwo", [text]).data["pelnomocnicy"][0]["imie_nazwisko"] == "Anna Kowal"


# --- przelew -----------------------------------------------------------------------------------

def test_przelew_inline_labels(fx):
    res = fx.extract("przelew", [PRZELEW_TEXT])
    assert res.data["przelew_bankowy"] == {
        "data_operacji": "2027-02-04", "kwota": 7500.0, "na_rachunek": "60101010100081362231000000",
        "numer_transakcji": "TST0000001", "tytul": "wpis od odwołania, Testbud, Gmina Testowo",
        "z_rachunku": "61109010140000071219812874",
    }


def test_przelew_ignores_print_date_and_fee(fx):
    text = ("POTWIERDZENIE TRANSAKCJI\nData wydruku: 10.02.2027\nOpłata: 0,00 PLN\nKwota: 20 000,00 PLN\n"
            "Data księgowania: 08.02.2027\nRachunek nadawcy: 61 1090 1014 0000 0712 1981 2874\n"
            "Rachunek odbiorcy: 60 1010 1010 0081 3622 3100 0000\nTytułem: WPIS OD ODW. ZAM: GMINA X, ZP.1.2027\n"
            "Status: zrealizowany")
    p = fx.extract("przelew", [text]).data["przelew_bankowy"]
    assert p["data_operacji"] == "2027-02-08" and p["kwota"] == 20000.0
    assert p["tytul"] == "WPIS OD ODW. ZAM: GMINA X, ZP.1.2027"
    assert p["numer_transakcji"] is None


def test_przelew_account_in_title_is_not_used_as_account_field(fx):
    text = ("Potwierdzenie wykonania przelewu\nData operacji: 2027-02-04\nKwota: 100,00 PLN\n"
            "Tytuł: zwrot na rachunek 11 1111 1111 1111 1111 1111 1111\nStatus: zrealizowany")
    p = fx.extract("przelew", [text]).data["przelew_bankowy"]
    assert p["na_rachunek"] is None and p["z_rachunku"] is None
    assert p["tytul"] == "zwrot na rachunek 11 1111 1111 1111 1111 1111 1111"


def test_przelew_invalid_date_and_short_account(fx):
    text = ("Potwierdzenie przelewu\nData operacji: 30.02.2027\nZ rachunku: PL61 1090 1014 0000\n"
            "Kwota: 50,00 PLN")
    p = fx.extract("przelew", [text]).data["przelew_bankowy"]
    assert p["data_operacji"] is None and p["z_rachunku"] is None and p["kwota"] == 50.0


def test_incomplete_document_gives_nulls_not_errors(fx):
    res = fx.extract("przelew", ["Potwierdzenie przelewu\nKwota: 10,00 zł"])
    assert res.validation_errors == []
    assert res.data["przelew_bankowy"]["kwota"] == 10.0
    assert res.data["przelew_bankowy"]["z_rachunku"] is None
    assert any("z_rachunku" in w for w in res.warnings)


def test_extractor_crash_still_returns_template(fx, monkeypatch):
    import odwolania2json.extract as ex

    def boom(ctx, cfg):
        raise RuntimeError("awaria")
    monkeypatch.setitem(ex.EXTRACTORS, "przelew", boom)
    res = fx.extract("przelew", ["cokolwiek"])
    assert res.data == {"przelew_bankowy": dict.fromkeys(
        ["data_operacji", "kwota", "na_rachunek", "numer_transakcji", "tytul", "z_rachunku"])}
    assert any("awaria" in w for w in res.warnings)


def test_unknown_label_is_not_supported(fx):
    assert not fx.supports("unknown")
    with pytest.raises(ValueError):
        fx.extract("unknown", ["x"])


# --- regresja na oficjalnych przykładach (znane szablony, nie dowód generalizacji) ------------

@pytest.mark.parametrize(("pdf", "label"), [
    ("p01_odwolanie.pdf", "odwolanie"), ("p42_odwolanie.pdf", "odwolanie"),
    ("p01_pelnomocnictwo.pdf", "pelnomocnictwo"), ("p01_potwierdzenie_przelewu.pdf", "przelew"),
])
def test_official_examples_match_reference_json(fx, pdf, label):
    gold = json.loads((FIXTURES / "official_gold" / pdf.replace(".pdf", ".json")).read_text(encoding="utf-8"))
    assert fx.extract(label, extract_text(OFFICIAL / pdf).pages).data == gold
