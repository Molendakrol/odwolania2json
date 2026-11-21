import pytest

from odwolania2json.classify import UNKNOWN, RuleClassifier, RuleConfigError
from odwolania2json.normalize import fold_diacritics

from .conftest import ODWOLANIE_TEXT, PELNOMOCNICTWO_TEXT, PRZELEW_TEXT, UNKNOWN_TEXT, doc_text


@pytest.mark.parametrize(
    ("text", "expected"),
    [(ODWOLANIE_TEXT, "odwolanie"), (PELNOMOCNICTWO_TEXT, "pelnomocnictwo"), (PRZELEW_TEXT, "przelew")],
)
def test_each_class_is_recognised(classifier, text, expected):
    res = classifier.classify(doc_text(text))
    assert res.label == expected
    assert res.reason == "match"
    assert res.signals[expected]  # decyzja ma uzasadnienie w postaci sygnałów


def test_unrelated_document_is_unknown(classifier):
    res = classifier.classify(doc_text(UNKNOWN_TEXT))
    assert res.label == UNKNOWN
    assert res.reason == "insufficient_signals"


def test_accidental_keywords_do_not_trigger_a_class(classifier):
    text = UNKNOWN_TEXT + (
        "\nW poprzednim numerze biuletynu pisaliśmy o tym, jak złożyć odwołanie, "
        "czym jest pełnomocnictwo i jak opłacić przelew za kurs. Kwota szkolenia to 100 zł."
    )
    res = classifier.classify(doc_text(text))
    assert res.label == UNKNOWN
    assert res.candidates == []


def test_single_strong_signal_is_not_enough(classifier):
    res = classifier.classify(doc_text("Pismo przewodnie\nZałączam pełnomocnictwo\nPEŁNOMOCNICTWO\nz poważaniem"))
    assert res.label == UNKNOWN


def test_conflicting_signals_give_unknown(classifier):
    mixed = (
        "Prezes Krajowej Izby Odwoławczej\nOdwołujący: Testbud\nZamawiający: Gmina Testowo\nODWOŁANIE\n"
        "PEŁNOMOCNICTWO\nMocodawca: Testbud, niniejszym udziela pełnomocnictwa adw. Annie Testowej"
    )
    res = classifier.classify(doc_text(mixed))
    assert set(res.candidates) == {"odwolanie", "pelnomocnictwo"}
    assert res.label == UNKNOWN
    assert res.reason.startswith("conflict:")


def test_clear_winner_despite_secondary_signals(classifier):
    # Odwołanie wspominające pełnomocnictwo w załącznikach nadal jest odwołaniem.
    text = ODWOLANIE_TEXT + "\nZałączniki:\n1. pełnomocnictwo udzielone pełnomocnikowi Odwołującego"
    assert classifier.classify(doc_text(text)).label == "odwolanie"


def test_kio_ruling_is_vetoed_even_with_appeal_vocabulary(classifier):
    text = (
        "Sygn. akt: KIO 1/27\nWYROK\nz dnia 1 marca 2027 r.\n"
        "Krajowa Izba Odwoławcza - w składzie: Przewodniczący: Jan Testowy\n"
        "po rozpoznaniu odwołania wniesionego do Prezesa Krajowej Izby Odwoławczej przez wykonawcę Testbud\n"
        "Odwołujący: Testbud\nZamawiający: Gmina Testowo\nODWOŁANIE\norzeka: 1. oddala odwołanie"
    )
    res = classifier.classify(doc_text(text))
    assert res.label == UNKNOWN
    assert res.reason == "veto:orzeczenie_kio"


def test_no_text_document_is_unknown(classifier):
    res = classifier.classify(doc_text("", sufficient=False))
    assert res.label == UNKNOWN
    assert res.reason == "no_text"


def test_text_without_polish_diacritics_is_still_classified(classifier):
    # Symulacja OCR, który zgubił polskie znaki.
    assert classifier.classify(doc_text(fold_diacritics(PELNOMOCNICTWO_TEXT))).label == "pelnomocnictwo"
    assert classifier.classify(doc_text(fold_diacritics(ODWOLANIE_TEXT))).label == "odwolanie"


def test_letter_spaced_title_is_recognised(classifier):
    text = PELNOMOCNICTWO_TEXT.replace("PEŁNOMOCNICTWO", "P E Ł N O M O C N I C T W O", 1)
    res = classifier.classify(doc_text(text))
    assert res.label == "pelnomocnictwo"
    assert "tytul_pelnomocnictwo" in res.signals["pelnomocnictwo"]


def test_classification_is_deterministic(classifier):
    results = {classifier.classify(doc_text(ODWOLANIE_TEXT)).to_dict().__repr__() for _ in range(5)}
    assert len(results) == 1


def test_labels_come_from_config(classifier):
    assert classifier.labels == ["odwolanie", "pelnomocnictwo", "przelew"]


# --- walidacja konfiguracji -------------------------------------------------------------------

def _cfg(**overrides):
    cfg = {
        "zones": {"head_chars": 1000, "body_pages": 3},
        "decision": {"min_margin": 1.0},
        "classes": {"a": {"min_score": 1, "min_signals": 1, "signals": [{"name": "s", "pattern": "x"}]}},
    }
    cfg.update(overrides)
    return cfg


def test_new_class_can_be_added_by_config_only():
    cfg = _cfg(classes={"faktura": {"min_score": 2, "min_signals": 2, "signals": [
        {"name": "tytul", "pattern": "(^|\\n)faktura", "zone": "head"},
        {"name": "nip", "pattern": "nip\\s*\\d{10}"},
    ]}})
    clf = RuleClassifier(cfg)
    assert clf.classify(doc_text("FAKTURA VAT\nNIP 1234567890")).label == "faktura"


@pytest.mark.parametrize(
    "bad",
    [
        {"classes": {"a": {"min_score": 1, "min_signals": 1, "signals": [{"name": "s", "pattern": "("}]}}},
        {"classes": {"a": {"min_score": 1, "min_signals": 1, "signals": [{"name": "s", "pattern": "x", "zone": "tail"}]}}},
        {"classes": {"a": {"min_score": 1, "min_signals": 1, "required_any": ["brak"], "signals": [{"name": "s", "pattern": "x"}]}}},
        {"classes": {"unknown": {"min_score": 1, "min_signals": 1, "signals": [{"name": "s", "pattern": "x"}]}}},
        {"classes": {}},
    ],
)
def test_invalid_config_is_rejected(bad):
    with pytest.raises(RuleConfigError):
        RuleClassifier(_cfg(**bad))
