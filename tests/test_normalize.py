from odwolania2json.normalize import clean_text, count_meaningful_chars, fold_diacritics, matching_text


def test_clean_text_collapses_spaces_and_blank_lines():
    raw = "Ala  ma\t\tkota\r\n\r\n\r\n\r\nKot   ma Alę  "
    assert clean_text(raw) == "Ala ma kota\n\nKot ma Alę"


def test_clean_text_removes_control_and_soft_hyphen():
    assert clean_text("wdro­żenie\x07") == "wdrożenie"


def test_matching_text_joins_letter_spaced_words():
    assert matching_text("P E Ł N O M O C N I C T W O") == "pełnomocnictwo"
    assert matching_text("O D W O Ł A N I E\nTreść") == "odwołanie\ntreść"


def test_matching_text_keeps_normal_short_words():
    assert matching_text("Ala i Ola w domu") == "ala i ola w domu"


def test_matching_text_joins_hyphenated_line_break():
    assert matching_text("oprogramo-\nwanie") == "oprogramowanie"


def test_fold_diacritics():
    assert fold_diacritics("Zażółć gęślą jaźń ŁÓDŹ") == "Zazolc gesla jazn LODZ"


def test_count_meaningful_chars():
    assert count_meaningful_chars(" a-b  1 \n") == 3
