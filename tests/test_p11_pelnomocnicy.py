"""P11: pełnomocnicy — kilka osób w jednej linii, lista linia po linii, tytuły, duplikaty, nazwiska spoza bloku.
Teksty pisane od zera."""

import pytest

from odwolania2json.extract import FieldExtractor

HEAD = ("PEŁNOMOCNICTWO\nDelta Serwis S.A. z siedzibą w Testowie, KRS: 0000123456, NIP: 5250000011, "
        "reprezentowana przez: Jan Kowal — Prezes Zarządu (dalej: „Mocodawca”), niniejszym udziela pełnomocnictwa:\n")
TAIL = ("do reprezentowania Mocodawcy w postępowaniu prowadzonym przez Zamawiającego — Gmina Testowo.\n"
        "Za Mocodawcę:\nJan Kowal — Prezes Zarządu\n")


@pytest.fixture(scope="module")
def fx():
    return FieldExtractor()


def names(fx, text):
    return [p["imie_nazwisko"] for p in fx.extract("pelnomocnictwo", [text]).data["pelnomocnicy"]]


def test_several_people_in_one_line_with_multiline_parentheses(fx):
    body = ("Anna Nowak (adwokat, nr wpisu PO-1) oraz Piotr Zając (radca prawny, nr\n"
            "wpisu WR-2) oraz Ewa Lis (radca prawny, nr wpisu WA-3),\n")
    assert names(fx, HEAD + body + TAIL) == ["Anna Nowak", "Piotr Zając", "Ewa Lis"]


def test_several_people_separated_by_commas_and_i(fx):
    assert names(fx, HEAD + "Anna Nowak, Piotr Zając i Ewa Lis\n" + TAIL) == ["Anna Nowak", "Piotr Zając", "Ewa Lis"]


def test_people_in_separate_lines(fx):
    body = "Anna Nowak\nPiotr Zając\no\n2. Ewa Lis — adwokat, nr wpisu KR-9,\n"
    assert names(fx, HEAD + body + TAIL) == ["Anna Nowak", "Piotr Zając", "Ewa Lis"]


def test_people_with_professional_titles(fx):
    body = "adw. Anna Nowak (nr wpisu PO-1), r. pr. Piotr\nZając (nr wpisu WR-2) oraz radca prawny Ewa Lis\n"
    assert names(fx, HEAD + body + TAIL) == ["Anna Nowak", "Piotr Zając", "Ewa Lis"]


def test_no_duplicates_when_both_mechanisms_find_the_same_person(fx):
    body = "adw. Anna Nowak oraz Piotr Zając oraz adw. Anna Nowak\n"
    assert names(fx, HEAD + body + TAIL) == ["Anna Nowak", "Piotr Zając"]


def test_order_follows_document(fx):
    body = "Piotr Zając (radca prawny) oraz adw. Anna Nowak\n"
    assert names(fx, HEAD + body + TAIL) == ["Piotr Zając", "Anna Nowak"]


def test_names_outside_the_attorney_block_are_not_taken(fx):
    # Reprezentant mocodawcy (przed frazą), zamawiający i podpis (po bloku) nie są pełnomocnikami.
    body = "Anna Nowak (adwokat)\n"
    got = names(fx, HEAD + body + TAIL + "Kontakt w sprawie: Marek Prokop — Dyrektor\n")
    assert got == ["Anna Nowak"]


def test_list_stops_at_first_ordinary_sentence(fx):
    text = ("PEŁNOMOCNICTWO\nDelta Serwis S.A., KRS 0000123456, NIP 5250000011\nPełnomocnicy\nadw. Anna Nowak\n"
            "Niniejszym upoważniam wskazanym pełnomocnikom do:\n• zastępowania Wykonawcy przed KIO,\n"
            "Mocodawca:\n.............\nJan Kowal\nPrezes Zarządu")
    assert names(fx, text) == ["Anna Nowak"]
