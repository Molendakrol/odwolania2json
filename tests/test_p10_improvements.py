"""Testy poprawek P10 (targeted extraction improvements). Każda nowa reguła: przypadek pozytywny i negatywny.
Teksty pisane od zera — nie są kopiami dokumentów DEV/HOLDOUT."""

import pytest

from odwolania2json.extract import FieldExtractor
from odwolania2json.extract.normalizers import (
    clean_person, drop_punct_tokens, fix_legal_form, fix_lone_prepositions, repair_email, strip_ocr_noise,
)

from .conftest import doc_text


@pytest.fixture(scope="module")
def fx():
    return FieldExtractor()


# --- B5: kontekstowe korekty OCR -------------------------------------------------------------------

@pytest.mark.parametrize(("raw", "fixed"), [
    ("Alfa Serwis Sp. z 0.0.", "Alfa Serwis Sp. z o.o."),
    ("Beta sp.Z o.0., ul. X", "Beta sp. z o.o., ul. X"),
    ("Gamma Sp. z o.o.", "Gamma Sp. z o.o."),                         # poprawny zapis bez zmian
    ("w dniu 10.0.2026 r. z 0.0. kwoty", "w dniu 10.0.2026 r. z 0.0. kwoty"),   # brak 'Sp.' -> bez zmian
])
def test_fix_legal_form(raw, fixed):
    assert fix_legal_form(raw) == fixed


@pytest.mark.parametrize(("raw", "fixed"), [
    ("wraz Z modernizacją sieci", "wraz z modernizacją sieci"),
    ("dostawa W formie elektronicznej", "dostawa w formie elektronicznej"),
    ("ul. W Warszawie", "ul. W Warszawie"),                            # po skrócie — bez zmian
    ("W dniu 5 maja", "W dniu 5 maja"),                                # początek zdania — bez zmian
    ("firma Z Kraków", "firma Z Kraków"),                              # po Z wielka litera — bez zmian
])
def test_fix_lone_prepositions(raw, fixed):
    assert fix_lone_prepositions(raw) == fixed


@pytest.mark.parametrize(("raw", "clean"), [
    (". : Kancelaria Lis sp. k.", "Kancelaria Lis sp. k."),
    ("Kancelaria Radcy Prawnego Jan Lis . e 4 :", "Kancelaria Radcy Prawnego Jan Lis"),
    ("< I : Delta Serwis S.A.", "Delta Serwis S.A."),
    ("Delta Serwis S.A. sd *", "Delta Serwis S.A."),
    ("Beta sp. p.", "Beta sp. p."),                                    # forma prawna nie jest szumem
    ("„ALFA” sp. z o.o.", "„ALFA” sp. z o.o."),
    ("Alfa S.A.", "Alfa S.A."),
])
def test_strip_ocr_noise(raw, clean):
    assert strip_ocr_noise(raw) == clean


def test_drop_punct_tokens_keeps_real_punctuation():
    assert drop_punct_tokens("Budowa mostu : | wraz Z drogą") == "Budowa mostu wraz z drogą"
    assert drop_punct_tokens("Dostawa sprzętu, etap I/II (część 2)") == "Dostawa sprzętu, etap I/II (część 2)"


@pytest.mark.parametrize(("raw", "email"), [
    ("j.lisGlis-kancelaria.example", "j.lis@lis-kancelaria.example"),
    ("J.Lis(lis.example", "j.lis@lis.example"),
    ("k.lisolis-kancelaria.example", "k.lis@lis-kancelaria.example"),          # jedyne 'o' -> jednoznaczne
    ("k.nowakonowak-kancelaria.example", None),  # trzy litery 'o' -> niejednoznaczne, brak zgadywania
    ("biuro@kancelaria.example", "biuro@kancelaria.example"),
    ("jan.kowalskiokolo.example", None),         # kilka kandydatów -> brak zgadywania
    ("tel. 000 000 000", None),
])
def test_repair_email(raw, email):
    assert repair_email(raw) == email


def test_clean_person_does_not_merge_names_across_titles():
    assert clean_person("radca prawny Anna Lis radca prawny Piotr Nowak") == "Anna Lis"
    assert clean_person("adw. Anna Lis adw. Piotr") == "Anna Lis"


# --- B1: odwołanie ---------------------------------------------------------------------------------

def _appeal(body: str, extra_head: str = "") -> str:
    return ("Warszawa, dnia 3 marca 2027 roku\nPrezes Krajowej Izby Odwoławczej\n"
            "Odwołujący:\nDelta Serwis S.A.\nul. Polna 1, 00-001 Testowo\nKRS: 0000123456, NIP: 525-000-00-11\n"
            "reprezentowany przez pełnomocnika:\nradca prawny Anna Lis\nradca prawny Piotr Nowak\n"
            "adres do korespondencji: : Kancelaria Radcy Prawnego Anna Lis . ul. Krótka 2, 00-002 Testowo\n"
            "e-mail: a.lisGlis-kancelaria.example\n" + extra_head +
            "Zamawiający:\nGmina Testowo\nul. Rynek 1, 00-003 Testowo\n"
            "Dotyczy: postępowania pn. „Budowa mostu wraz Z : drogą”, nr referencyjny: ZP.1.2027\n"
            "ODWOŁANIE\nDziałając w imieniu Odwołującego, " + body)


def test_appeal_first_listed_action_sets_category_and_date(fx):
    text = _appeal("wnoszę odwołanie wobec:\n1. czynności unieważnienia postępowania, o której Zamawiający "
                   "poinformował w dniu 4 lutego 2027 roku;\n2. zaniechania wezwania do wyjaśnień;")
    d = fx.extract("odwolanie", [text]).data
    assert d["OdwolanieKIO"]["czynnosc_powod_id"] == 9 and d["OdwolanieKIO"]["czynnosc_dzien"] == "2027-02-04"
    pel = d["Odwolujacy"][0]["pelnomocnik"]
    assert pel == {"imie_nazwisko": "Anna Lis", "nazwa": "Kancelaria Radcy Prawnego Anna Lis",
                   "mail": "a.lis@lis-kancelaria.example"}
    assert d["ZamowieniePubliczne"]["tytul"] == "Budowa mostu wraz z drogą"


def test_appeal_list_uses_first_item_not_later_ones(fx):
    text = _appeal("wnoszę odwołanie wobec:\n1. treści SWZ opublikowanej w dniu 2 stycznia 2027 r.;\n"
                   "2. czynności odrzucenia oferty z dnia 9 stycznia 2027 r.;")
    kio = fx.extract("odwolanie", [text]).data["OdwolanieKIO"]
    assert kio["czynnosc_powod_id"] == 4 and kio["czynnosc_dzien"] == "2027-01-02"


def test_zarzut_nr_1_still_has_priority_over_list(fx):
    text = _appeal("wnoszę odwołanie wobec:\n1. treści SWZ opublikowanej w dniu 2 stycznia 2027 r.;\n"
                   "Zarzut nr 1 – zaniechania odrzucenia oferty wykonawcy X, tj. naruszenie art. 226 Pzp")
    assert fx.extract("odwolanie", [text]).data["OdwolanieKIO"]["czynnosc_powod_id"] == 7


def test_appeal_list_with_unrecognised_action_gives_null(fx):
    text = _appeal("wnoszę odwołanie wobec:\n1. naruszenia zasady przejrzystości postępowania;\n2. innych czynności;")
    kio = fx.extract("odwolanie", [text]).data["OdwolanieKIO"]
    assert kio["czynnosc_powod_id"] is None and kio["czynnosc_opis"] is None


def test_info_about_challenged_action_gives_date_fallback(fx):
    text = _appeal("wnoszę odwołanie wobec czynności odrzucenia oferty Odwołującego. Informacja o zaskarżonej "
                   "czynności została przekazana Odwołującemu w dniu 06.01.2027 r. Odwołanie wniesiono w terminie.")
    kio = fx.extract("odwolanie", [text]).data["OdwolanieKIO"]
    assert kio["czynnosc_powod_id"] == 1 and kio["czynnosc_dzien"] == "2027-01-06"


def test_enumerated_joint_contracting_authorities(fx):
    head = ("Zamawiający:\nZamawiający wspólnie udzielający zamówienia:\n1) Zarząd Dróg w Testowie\nul. Polna 3, "
            "00-010 Testowo\nNIP: 525-000-00-22\n— Zamawiający prowadzący postępowanie\n2) Zakład Komunalny w Testowie\n"
            "ul. Szkolna 4, 00-011 Testowo\nREGON: 123456789\n")
    text = _appeal("wnoszę odwołanie.", extra_head="").replace("Zamawiający:\nGmina Testowo\nul. Rynek 1, 00-003 Testowo\n", head)
    zam = fx.extract("odwolanie", [text]).data["Zamawiajacy"]
    assert [(z["nazwa"], z["nip"], z["regon"], z["prowadzacy_postepowanie"]) for z in zam] == [
        ("Zarząd Dróg w Testowie", "5250000022", None, 1),
        ("Zakład Komunalny w Testowie", None, "123456789", 0),
    ]


def test_single_contracting_authority_with_number_in_address_is_not_split(fx):
    text = _appeal("wnoszę odwołanie.").replace("ul. Rynek 1, 00-003", "ul. Rynek 1) lokal 2, 00-003")
    zam = fx.extract("odwolanie", [text]).data["Zamawiajacy"]
    assert len(zam) == 1 and zam[0]["nazwa"] == "Gmina Testowo" and zam[0]["prowadzacy_postepowanie"] is None


# --- B2/B3: pełnomocnictwo --------------------------------------------------------------------------

POA = ("Testowo, 10.02.2027 r.\nPEŁNOMOCNICTWO\nDelta Serwis Sp. z 0.0. z siedzibą w Testowie, ul. Polna 1, "
       "KRS: 0000123456, NIP: 5250000011, reprezentowana przez: Jan Kowal — Prezes Zarządu, Ewa Lis — Członek "
       "Zarządu (dalej: „Mocodawca”), niniejszym udziela\n:\npełnomocnictwa:\n1. Anna Nowak — adwokat, nr wpisu WA-1,\n"
       "II. Piotr Zając (radca prawny, nr wpisu KR-2)\no\ndo reprezentowania Mocodawcy przed Krajową Izbą Odwoławczą.\n")


def test_poa_people_listed_line_by_line_and_representatives(fx):
    d = fx.extract("pelnomocnictwo", [POA + "Za Mocodawcę:\nEwa Lis — Członek Zarządu"]).data
    assert d["mocodawca"][0]["data"]["nazwa"] == "Delta Serwis Sp. z o.o."
    assert [p["imie_nazwisko"] for p in d["mocodawca"][0]["podpisujacy"]] == ["Jan Kowal", "Ewa Lis"]
    assert [p["imie_nazwisko"] for p in d["pelnomocnicy"]] == ["Anna Nowak", "Piotr Zając"]


def test_poa_electronic_signature_when_no_representatives(fx):
    text = POA.replace("reprezentowana przez: Jan Kowal — Prezes Zarządu, Ewa Lis — Członek Zarządu ", "")
    d = fx.extract("pelnomocnictwo", [text + "Signature Not Verified\nDokument podpisany przez Jan Kowal\n"]).data
    assert d["mocodawca"][0]["podpisujacy"] == [{"imie_nazwisko": "Jan Kowal"}]


def test_poa_dotted_signatures_take_priority_over_representatives(fx):
    text = POA + "Mocodawca:\n..............................\nMarek Prokop\nProkurent\n"
    d = fx.extract("pelnomocnictwo", [text]).data
    assert d["mocodawca"][0]["podpisujacy"] == [{"imie_nazwisko": "Marek Prokop"}]


def test_poa_line_mode_does_not_take_signers_after_unrelated_line(fx):
    text = ("PEŁNOMOCNICTWO\nDelta Serwis S.A., KRS 0000123456, NIP 5250000011\nPełnomocnicy\nadw. Anna Nowak\n"
            "Niniejszym upoważniam wskazanym pełnomocnikom do:\n• zastępowania Wykonawcy przed KIO,\n"
            "Mocodawca:\n.............\nJan Kowal\nPrezes Zarządu")
    d = fx.extract("pelnomocnictwo", [text]).data
    assert [p["imie_nazwisko"] for p in d["pelnomocnicy"]] == ["Anna Nowak"]          # bez 'Jan Kowal'


def test_poa_represented_by_signatories_below_gives_no_names_from_that_phrase(fx):
    text = ("PEŁNOMOCNICTWO\nDelta Serwis S.A., KRS 0000123456, NIP 5250000011 (dalej: „Mocodawca”), reprezentowana "
            "przez osoby podpisane poniżej, udziela pełnomocnictwa: adw. Anna Nowak do reprezentowania Mocodawcy.")
    d = fx.extract("pelnomocnictwo", [text]).data
    assert d["mocodawca"][0]["podpisujacy"] == [{"imie_nazwisko": None}]


# --- B4: przelew ------------------------------------------------------------------------------------

TABLE_TRANSFER = ("Bank Testowy\nPotwierdzenie transakcji\nOpis\nwpis od odwołania - postępowanie nr ZP.1.2027\n"
                  "Kwota\nData transakcji\n7 500,00 PLN\n11.01.2027\nDane nadawcy\nNazwa i adres\nNumer rachunku\n"
                  "Nazwa banku\nDELTA SERWIS S.A., UL. POLNA 1, 00-001 TESTOWO\nPL61 1090 1014 0000 0712 1981 2874\n"
                  "BANK TESTOWY\nDane odbiorcy\nNazwa i adres\nNumer rachunku\nNazwa banku\n"
                  "Urząd Zamówień Publicznych, ul. Postępu 17A\n60 1010 1010 0081 3622 3100 0000\n")


def test_transfer_table_layout_accounts(fx):
    p = fx.extract("przelew", [TABLE_TRANSFER]).data["przelew_bankowy"]
    assert p["z_rachunku"] == "61109010140000071219812874" and p["na_rachunek"] == "60101010100081362231000000"
    assert p["kwota"] == 7500.0 and p["data_operacji"] == "2027-01-11"


def test_transfer_sender_label_does_not_take_recipient_account(fx):
    text = TABLE_TRANSFER.replace("PL61 1090 1014 0000 0712 1981 2874\n", "")
    p = fx.extract("przelew", [text]).data["przelew_bankowy"]
    assert p["na_rachunek"] == "60101010100081362231000000" and p["z_rachunku"] is None


def test_transfer_title_skips_ocr_noise_and_stops_at_footer(fx):
    text = ("Potwierdzenie przelewu\nZ rachunku: 61 1090 1014 0000 0712 1981 2874\nKwota: 17,00 PLN\n"
            "Szczegoly platnosci\n!\nOPŁATA SKARBOWA OD PEŁNOMOCNICTWA DLA\n:\nRADCA PRAWNY JAN LIS\n.\n"
            "Dokument podpisany przez Bank Testowy S.A.\n")
    p = fx.extract("przelew", [text]).data["przelew_bankowy"]
    assert p["tytul"] == "OPŁATA SKARBOWA OD PEŁNOMOCNICTWA DLA RADCA PRAWNY JAN LIS"


def test_transfer_spaced_date_only_after_date_label(fx):
    ok = fx.extract("przelew", ["Potwierdzenie przelewu\nZlecona data wykonania\n20 04 2027\nKwota: 1,00 zł"]).data
    assert ok["przelew_bankowy"]["data_operacji"] == "2027-04-20"
    no = fx.extract("przelew", ["Potwierdzenie przelewu\nNr klienta 20 04 2027\nKwota: 1,00 zł"]).data
    assert no["przelew_bankowy"]["data_operacji"] is None


def test_transfer_single_unlabelled_amount_and_ambiguous_amounts(fx):
    one = fx.extract("przelew", ["Szczegóły zlecenia\nDane zlecenia\n17.00 PLN\nOpłata 0,00 PLN"]).data
    assert one["przelew_bankowy"]["kwota"] == 17.0
    two = fx.extract("przelew", ["Szczegóły zlecenia\n17.00 PLN\n250,00 PLN"]).data
    assert two["przelew_bankowy"]["kwota"] is None


# --- klasyfikator: słownictwo przelewu --------------------------------------------------------------

def test_classifier_recognises_bank_order_vocabulary(classifier):
    text = ("Bank Testowy\nSzczegóły zlecenia\nTyp zlecenia\nPrzelew krajowy\nRachunek zleceniodawcy\n"
            "11 1240 1111 9078 8570 5000 0455\nRachunek kontrahenta\n25 1030 1508 9486 5035 5622 1675\n17.00 PLN")
    assert classifier.classify(doc_text(text)).label == "przelew"


def test_classifier_amount_and_one_account_in_letter_is_not_transfer(classifier):
    text = ("Pismo w sprawie zwrotu wadium\nUprzejmie informujemy, że wadium w kwocie 1 000,00 zł zostanie zwrócone "
            "na rachunek 11 1240 1111 9078 8570 5000 0455 po zakończeniu postępowania. Z poważaniem, Zamawiający.")
    assert classifier.classify(doc_text(text)).label == "unknown"
