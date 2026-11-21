import pytest

from odwolania2json.extract.normalizers import (
    clean_person, normalize_account, normalize_bzp, normalize_ted, parse_amount, parse_date,
)


@pytest.mark.parametrize(("text", "iso"), [
    ("Gdańsk, dnia 3 września 2026 roku", "2026-09-03"),
    ("02.07.2026", "2026-07-02"),
    ("11-10-2026", "2026-10-11"),
    ("2026-03-21", "2026-03-21"),
    ("3/9/2026", "2026-09-03"),
    ("3 IX 2026", "2026-09-03"),
    ("31.02.2026", None),          # nieistniejąca data
    ("brak daty", None),
])
def test_parse_date(text, iso):
    assert parse_date(text) == iso


@pytest.mark.parametrize(("text", "value"), [
    ("7 500,00 PLN", 7500.0), ("15.000,00 zł", 15000.0), ("PLN 200.00", 200.0), ("34.00", 34.0),
    ("1 234 567,89", 1234567.89), ("100", 100.0), ("brak", None),
])
def test_parse_amount(text, value):
    assert parse_amount(text) == value


def test_normalize_account():
    assert normalize_account("PL61 1090 1014 0000 0712 1981 2874") == "61109010140000071219812874"
    assert normalize_account("nr 61109010140000071219812874,") == "61109010140000071219812874"
    assert normalize_account("123 456") is None


@pytest.mark.parametrize(("text", "ted"), [
    ("2026/S 086-999073", "999073-2026"), ("2026/S086-999073", "999073-2026"),
    ("TED: 999137-2026", "999137-2026"), ("2025/S 112-383751", "383751-2025"), ("brak", None),
])
def test_normalize_ted(text, ted):
    assert normalize_ted(text) == ted


def test_normalize_bzp():
    assert normalize_bzp("Nr ogłoszenia w BZP: 2025/BZP 00270698 z dnia") == "2025/BZP00270698"
    assert normalize_bzp("2026/BZP00999497/03") == "2026/BZP00999497/03"
    assert normalize_bzp("999073-2026") is None


@pytest.mark.parametrize(("text", "person"), [
    ("r. pr. Aleksandra Marciniak (nr wpisu", "Aleksandra Marciniak"),
    ("adw. Anna Kowalska-Nowak,", "Anna Kowalska-Nowak"),
    ("radcę prawnego Jan Kowalski", "Jan Kowalski"),
    ("Panasiuk Jan", "Panasiuk Jan"),     # „Pan…” w nazwisku nie jest tytułem
])
def test_clean_person(text, person):
    assert clean_person(text) == person
