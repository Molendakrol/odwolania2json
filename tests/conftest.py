from __future__ import annotations

import html
from pathlib import Path

import pymupdf
import pytest

from odwolania2json.classify import DocumentText, RuleClassifier

FIXTURES = Path(__file__).parent / "fixtures"
OFFICIAL = FIXTURES / "official"


def make_pdf(path: Path, pages: list[str]) -> Path:
    """Tworzy PDF z warstwą tekstową (obsługa polskich znaków); pusta strona = "" ."""
    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page()
        if text:
            body = "<br>".join(html.escape(line) for line in text.split("\n"))
            page.insert_htmlbox(pymupdf.Rect(40, 40, 555, 800), f"<div style='font-size:9px'>{body}</div>")
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)
    doc.close()
    return path


def make_scan_pdf(path: Path, pages: list[str], dpi: int = 200) -> Path:
    """PDF „zeskanowany”: każda strona to wyłącznie obraz (brak warstwy tekstowej); pusta strona = biały obraz."""
    src = pymupdf.open(make_pdf(path.with_suffix(".src.pdf"), pages))
    out = pymupdf.open()
    for page in src:
        pix = page.get_pixmap(dpi=dpi)
        new = out.new_page(width=page.rect.width, height=page.rect.height)
        new.insert_image(new.rect, pixmap=pix)
    out.save(path)
    out.close()
    src.close()
    path.with_suffix(".src.pdf").unlink()
    return path


class FakeOcr:
    """Deterministyczna atrapa silnika OCR: zwraca zadany tekst per numer strony (od 0)."""

    name = "fake-ocr"
    language = "pol"
    dpi = 300

    def __init__(self, texts: dict[int, str] | None = None, fail_pages: set[int] = frozenset(), available: bool = True):
        self.texts = texts or {}
        self.fail_pages = set(fail_pages)
        self.available = available
        self.calls: list[int] = []

    def is_available(self):
        return (True, "fake") if self.available else (False, "brak danych języka (atrapa)")

    def ocr_page(self, page):
        self.calls.append(page.number)
        if page.number in self.fail_pages:
            raise RuntimeError("błąd renderowania (atrapa)")
        return self.texts.get(page.number, "")


def doc_text(text: str, sufficient: bool = True) -> DocumentText:
    return DocumentText(pages=[text], has_sufficient_text=sufficient)


@pytest.fixture(scope="session")
def classifier() -> RuleClassifier:
    return RuleClassifier.from_file()


# --- Teksty testowe pisane od zera (inne układy niż w danych organizatora) ---------------

ODWOLANIE_TEXT = """Kancelaria Radcy Prawnego Anna Testowa
Szczecin, 5.02.2027 r.
Do Prezesa Krajowej Izby Odwoławczej
ul. Postępu 17a, 02-676 Warszawa
Odwołujący: Testbud Sp. z o.o., ul. Portowa 1, 70-001 Szczecin, KRS 0000000001
Zamawiający: Gmina Testowo, ul. Rynek 1, 70-002 Testowo
ODWOŁANIE
od czynności Zamawiającego polegającej na odrzuceniu oferty Odwołującego
Działając na podstawie art. 513 pkt 1 ustawy Pzp, wnoszę odwołanie i zarzucam Zamawiającemu naruszenie:
1. art. 226 ust. 1 pkt 5 Pzp – zarzut bezpodstawnego odrzucenia oferty,
2. art. 16 pkt 1 Pzp.
Wnoszę o uwzględnienie odwołania w całości."""

PELNOMOCNICTWO_TEXT = """PEŁNOMOCNICTWO
Testbud Sp. z o.o. z siedzibą w Szczecinie (KRS 0000000001, NIP 1234567890), zwana dalej Mocodawcą,
reprezentowana przez Prezesa Zarządu Jana Próbnego, niniejszym udziela pełnomocnictwa
radcy prawnemu Annie Testowej do reprezentowania Mocodawcy w postępowaniu odwoławczym
przed Krajową Izbą Odwoławczą, w tym do wniesienia odwołania.
Szczecin, 4 lutego 2027 r.
Jan Próbny – Prezes Zarządu"""

PRZELEW_TEXT = """Bank Testowy S.A.
Potwierdzenie wykonania przelewu
Data operacji: 2027-02-04
Z rachunku: PL61 1090 1014 0000 0712 1981 2874
Na rachunek: 60 1010 1010 0081 3622 3100 0000
Odbiorca: Urząd Zamówień Publicznych
Kwota: 7 500,00 PLN
Tytuł: wpis od odwołania, Testbud, Gmina Testowo
Numer referencyjny: TST0000001
Status: zrealizowany"""

UNKNOWN_TEXT = """Zaproszenie na szkolenie
Uprzejmie zapraszamy pracowników urzędu na szkolenie z obsługi nowego systemu kadrowego,
które odbędzie się w sali konferencyjnej. Prosimy o potwierdzenie obecności do piątku.
Program obejmuje omówienie modułu urlopów, delegacji oraz ewidencji czasu pracy.
Szkolenie poprowadzi zewnętrzny trener. Uczestnicy otrzymają materiały w wersji elektronicznej."""
