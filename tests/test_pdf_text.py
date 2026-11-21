from odwolania2json.pdf_text import ExtractionStatus, extract_text

from .conftest import OFFICIAL, make_pdf


def test_extracts_text_with_polish_characters_and_page_count(tmp_path):
    pdf = make_pdf(tmp_path / "a.pdf", ["Pierwsza strona: zażółć gęślą jaźń. " * 10, "Druga strona dokumentu. " * 10])
    res = extract_text(pdf)
    assert res.status is ExtractionStatus.OK
    assert res.page_count == 2
    assert res.pages_with_text == 2
    assert "zażółć gęślą jaźń" in res.pages[0]
    assert res.text.index("Pierwsza strona") < res.text.index("Druga strona")
    assert res.char_count == len(res.text)
    assert res.error is None


def test_preserves_reading_order_within_page(tmp_path):
    pdf = make_pdf(tmp_path / "order.pdf", ["NAGŁÓWEK\n" + "linia treści\n" * 30 + "STOPKA"])
    text = extract_text(pdf).text
    assert text.index("NAGŁÓWEK") < text.index("linia treści") < text.index("STOPKA")


def test_pdf_without_text_layer_is_no_text(tmp_path):
    res = extract_text(make_pdf(tmp_path / "empty.pdf", ["", ""]))
    assert res.status is ExtractionStatus.NO_TEXT
    assert res.page_count == 2
    assert res.meaningful_chars == 0
    assert not res.has_sufficient_text


def test_pdf_with_little_text_is_low_text(tmp_path):
    res = extract_text(make_pdf(tmp_path / "short.pdf", ["Tylko krótka notatka."]))
    assert res.status is ExtractionStatus.LOW_TEXT
    assert not res.has_sufficient_text


def test_corrupted_file_returns_error_not_exception(tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.4\nto nie jest poprawny PDF")
    res = extract_text(bad)
    assert res.status is ExtractionStatus.ERROR
    assert res.error


def test_missing_file_returns_error(tmp_path):
    res = extract_text(tmp_path / "brak.pdf")
    assert res.status is ExtractionStatus.ERROR
    assert res.error


def test_scanned_official_negative_has_no_text():
    res = extract_text(OFFICIAL / "neg_skan_bez_tekstu.pdf")
    assert res.status is ExtractionStatus.NO_TEXT
    assert res.page_count == 1


def test_diagnostics_excludes_full_text(tmp_path):
    res = extract_text(make_pdf(tmp_path / "d.pdf", ["Treść dokumentu testowego. " * 20]))
    diag = res.diagnostics()
    assert "pages" not in diag
    assert diag["status"] == "ok" and diag["has_sufficient_text"] is True
